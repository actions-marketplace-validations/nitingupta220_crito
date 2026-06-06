#!/usr/bin/env python3
"""
AI PR Review — Self-contained GitHub Actions runner.

Drop this file + pr-review.yml into ANY repo. Zero external imports.
Requires: GITHUB_TOKEN, OPENROUTER_API_KEY in repo secrets.
Dependencies: httpx (installed via action_requirements.txt)
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from typing import Any

import httpx

# ── Config ──────────────────────────────────────────────────────────────────

MODELS: list[str] = [m.strip() for m in os.getenv(
    "FREE_MODEL_CHAIN",
    "moonshotai/kimi-k2.6:free,qwen/qwen3-next-80b-a3b-instruct:free,meta-llama/llama-3.3-70b-instruct:free",
).split(",") if m.strip()]

GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]

GITHUB_API = "https://api.github.com"
OPENROUTER_API = "https://openrouter.ai/api/v1/chat/completions"

MAX_DIFF_CHARS = 28_000   # token budget safety cap
MAX_FILES = 40            # ignore giant PRs

SKIP_PATTERNS = re.compile(
    r"\.(lock|min\.js|min\.css|generated\.|pb\.go|pb2\.py|snap|svg|png|jpg|gif|ico|woff|woff2|ttf|eot)$",
    re.IGNORECASE,
)

ANTI_FP = """
STRICT RULES — violations invalidate your entire response:
1. Only flag issues that are CLEARLY present in the diff (+lines). Never guess.
2. Skip style nits (spacing, naming preferences, casing).
3. Skip informational comments, docstrings, or logging unless they expose secrets.
4. Do NOT flag theoretical future problems — only concrete, current bugs.
5. Output ONLY valid JSON. No markdown fences, no prose outside JSON.
6. If nothing significant is found, return an empty findings array [].
""".strip()

# ── GitHub helpers ───────────────────────────────────────────────────────────

def gh_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def github_get(client: httpx.AsyncClient, path: str) -> Any:
    r = await client.get(f"{GITHUB_API}{path}", headers=gh_headers(), timeout=30)
    r.raise_for_status()
    return r.json()


async def github_post(client: httpx.AsyncClient, path: str, body: dict) -> Any:
    r = await client.post(
        f"{GITHUB_API}{path}",
        headers=gh_headers(),
        json=body,
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


# Extensions that are likely source code (reviewed first for better token budget use)
_SOURCE_EXTS = re.compile(r"\.(py|js|ts|tsx|jsx|go|java|rb|rs|cs|cpp|c|h|php|swift|kt|scala)$", re.IGNORECASE)
_CONFIG_EXTS = re.compile(r"\.(yml|yaml|json|toml|ini|cfg|md|txt)$", re.IGNORECASE)


def _sort_files(files: list[dict]) -> list[dict]:
    """Prioritize source code files so they're reviewed first within the token budget."""
    source = [f for f in files if _SOURCE_EXTS.search(f.get("filename", ""))]
    config = [f for f in files if _CONFIG_EXTS.search(f.get("filename", ""))]
    other = [f for f in files if f not in source and f not in config]
    return source + other + config


async def get_pr_diff(client: httpx.AsyncClient, repo: str, pr_number: int) -> str:
    """Fetch the unified diff for a PR, filtered and capped. Source files come first."""
    files: list[dict] = await github_get(client, f"/repos/{repo}/pulls/{pr_number}/files")
    files = _sort_files(files[:MAX_FILES])
    chunks: list[str] = []
    total = 0
    for f in files:
        filename = f.get("filename", "")
        if SKIP_PATTERNS.search(filename):
            continue
        patch = f.get("patch", "")
        if not patch:
            continue
        chunk = f"### {filename}\n{patch}\n"
        if total + len(chunk) > MAX_DIFF_CHARS:
            chunks.append(f"### {filename}\n[diff truncated — too large]\n")
            break
        chunks.append(chunk)
        total += len(chunk)
    return "\n".join(chunks) or "(no diff available)"


# ── OpenRouter helpers ───────────────────────────────────────────────────────

async def call_llm(client: httpx.AsyncClient, system: str, user: str) -> dict:
    """Call OpenRouter with model fallback + retry backoff. Returns parsed JSON dict."""
    last_err: Exception | None = None

    for model in MODELS:
        payload: dict = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": 1200,
            "temperature": 0.1,
            # Note: no response_format — free models don't support json_object mode.
            # Our 4-stage _parse_json() handles messy output robustly.
        }

        # Retry the same model up to 2 times for transient 429/503 errors
        for attempt in range(2):
            try:
                r = await client.post(
                    OPENROUTER_API,
                    headers={
                        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                        "Content-Type": "application/json",
                        "HTTP-Referer": "https://github.com/ai-pr-review",
                        "X-Title": "AI PR Review",
                    },
                    json=payload,
                    timeout=90,
                )
                if r.status_code in (429, 503):
                    wait = 5 * (attempt + 1)
                    print(f"[WARN] {model} → HTTP {r.status_code}, retrying in {wait}s...", flush=True)
                    await asyncio.sleep(wait)
                    last_err = Exception(f"HTTP {r.status_code} on {model}")
                    continue
                r.raise_for_status()
                content = r.json()["choices"][0]["message"]["content"]
                return _parse_json(content)
            except (Exception,) as e:
                last_err = e
                if attempt == 0:
                    await asyncio.sleep(3)  # brief pause before retry
                continue
        # All retries exhausted for this model, try next

    raise RuntimeError(f"All models failed. Last error: {last_err}")


def _parse_json(raw: str) -> dict:
    """4-stage defensive JSON parser."""
    # Stage 1: direct parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # Stage 2: strip markdown fences
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # Stage 3: extract first JSON object
    m = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    # Stage 4: fallback empty
    return {"findings": [], "summary": raw[:300]}


# ── Specialist Agents ────────────────────────────────────────────────────────

AGENT_CONFIGS = {
    "security": {
        "emoji": "🔒",
        "focus": (
            "Look for: hardcoded secrets/API keys, SQL injection, command injection, "
            "path traversal, insecure deserialization, weak cryptography (MD5/SHA1 for passwords), "
            "missing auth checks, SSRF, XSS, open redirects."
        ),
    },
    "bug": {
        "emoji": "🐛",
        "focus": (
            "Look for: null/None dereference, off-by-one errors, unclosed resources (files/DB connections), "
            "race conditions, infinite loops, incorrect error handling, bare except clauses, "
            "missing error propagation, wrong type assumptions."
        ),
    },
    "performance": {
        "emoji": "⚡",
        "focus": (
            "Look for: N+1 database queries, missing indexes implied by query patterns, "
            "O(n²) or worse algorithms, missing HTTP timeouts, blocking I/O in async context, "
            "unbounded memory growth, missing pagination."
        ),
    },
    "quality": {
        "emoji": "📋",
        "focus": (
            "Look for: dead code, unreachable branches, duplicate logic, overly complex functions "
            "(>50 lines doing multiple things), missing input validation, magic numbers without constants, "
            "inconsistent error handling patterns."
        ),
    },
    "docs": {
        "emoji": "📚",
        "focus": (
            "Look for: public functions/classes without docstrings, misleading or outdated comments, "
            "missing parameter/return type documentation on complex functions, "
            "TODO/FIXME without tracking issues."
        ),
    },
}


async def run_agent(
    client: httpx.AsyncClient,
    name: str,
    config: dict,
    diff: str,
    pr_title: str,
) -> dict:
    system = f"""You are a senior {name} reviewer. Analyze ONLY the added lines (+) in the diff.
{config['focus']}

{ANTI_FP}

Return JSON: {{"findings": [{{"severity": "high|medium|low", "file": "...", "line": "...", "issue": "...", "suggestion": "..."}}], "summary": "one sentence"}}"""

    user = f"PR: {pr_title}\n\nDiff:\n{diff}"
    try:
        result = await call_llm(client, system, user)
        result["agent"] = name
        result["emoji"] = config["emoji"]
        return result
    except Exception as e:
        return {"agent": name, "emoji": config["emoji"], "findings": [], "summary": f"Agent error: {e}"}


# ── Aggregator ───────────────────────────────────────────────────────────────

async def aggregate(
    client: httpx.AsyncClient,
    agent_results: list[dict],
    pr_title: str,
    diff: str,
) -> dict:
    findings_summary = "\n".join(
        f"[{r['agent'].upper()}] {r.get('summary', '')}\nFindings: {json.dumps(r.get('findings', []))}"
        for r in agent_results
    )
    system = """You are a principal engineer synthesizing a PR review from 5 specialist agents.
Combine and deduplicate findings. Filter out false positives.
Return JSON: {
  "overall": "one paragraph overall assessment",
  "verdict": "approved|needs_changes|info",
  "strengths": ["..."],
  "critical": [{"file":"","issue":"","suggestion":""}],
  "warnings": [{"file":"","issue":"","suggestion":""}]
}"""
    user = f"PR: {pr_title}\n\nAgent reports:\n{findings_summary}"
    try:
        return await call_llm(client, system, user)
    except Exception as e:
        return {"overall": f"Aggregation error: {e}", "verdict": "info", "strengths": [], "critical": [], "warnings": []}


# ── Comment formatter ────────────────────────────────────────────────────────

def format_comment(agent_results: list[dict], agg: dict) -> str:
    verdict_map = {"approved": "✅ Approved", "needs_changes": "⚠️ Needs Changes", "info": "ℹ️ Info"}
    verdict = verdict_map.get(agg.get("verdict", "info"), "ℹ️ Info")

    lines = [
        "## 🤖 PR Review Summary",
        "",
        f"### {verdict} — Overall Assessment",
        agg.get("overall", ""),
        "",
        "### 🔍 Agent Findings",
        "",
    ]

    for r in agent_results:
        emoji = r.get("emoji", "🔹")
        name = r.get("agent", "agent").title()
        findings = r.get("findings", [])
        lines.append(f"#### {emoji} {name}")
        if not findings:
            lines.append("- No significant issues found.")
        else:
            for f in findings[:5]:  # cap per agent
                sev = f.get("severity", "low").upper()
                icon = {"HIGH": "🔴", "MEDIUM": "🟠", "LOW": "🟡"}.get(sev, "🔵")
                lines.append(f"- {icon} **[{sev}]** `{f.get('file', '')}:{f.get('line', '?')}` — {f.get('issue', '')}")
                if f.get("suggestion"):
                    lines.append(f"  - 💡 {f['suggestion']}")
        lines.append("")

    # Critical & Warnings
    critical = agg.get("critical", [])
    warnings = agg.get("warnings", [])

    if critical:
        lines += ["### 🔴 Critical Issues", ""]
        for c in critical[:5]:
            lines.append(f"- **`{c.get('file', '')}`** — {c.get('issue', '')}")
            if c.get("suggestion"):
                lines.append(f"  - 💡 {c['suggestion']}")
        lines.append("")

    strengths = agg.get("strengths", [])
    if strengths:
        lines += ["### 💪 Strengths", ""]
        for s in strengths[:4]:
            lines.append(f"- {s}")
        lines.append("")

    if not critical and not warnings:
        lines += ["### ✅ Recommendations", "- No changes required — this PR is ready for merge.", ""]

    lines += [
        "---",
        "*Powered by [AI PR Review](https://github.com/Swapnill1435/review-agent) • 5 specialist agents • OpenRouter free models*",
    ]

    return "\n".join(lines)


# ── Main ─────────────────────────────────────────────────────────────────────

async def main() -> None:
    # Parse event from env
    event_name = os.getenv("GITHUB_EVENT_NAME", "")
    event_path = os.getenv("GITHUB_EVENT_PATH", "")
    repo = os.getenv("GITHUB_REPOSITORY", "")

    if not event_path or not os.path.exists(event_path):
        print("[ERROR] GITHUB_EVENT_PATH not set or file missing", flush=True)
        sys.exit(1)

    with open(event_path) as f:
        event = json.load(f)

    # Determine PR number
    pr_number: int | None = None
    if event_name == "pull_request":
        pr_number = event["pull_request"]["number"]
        pr_title = event["pull_request"]["title"]
        is_draft = event["pull_request"].get("draft", False)
        if is_draft:
            print("[INFO] Skipping draft PR", flush=True)
            return
    elif event_name == "issue_comment":
        pr_number = event["issue"]["number"]
        pr_title = event["issue"]["title"]
        comment_body = event.get("comment", {}).get("body", "")
        if "/review" not in comment_body:
            print("[INFO] Comment does not contain /review, skipping", flush=True)
            return
    else:
        print(f"[WARN] Unsupported event: {event_name}", flush=True)
        sys.exit(0)

    print(f"[INFO] Event: {event_name} | Repo: {repo} | PR: #{pr_number}", flush=True)

    async with httpx.AsyncClient() as client:
        # Fetch diff
        print("[INFO] Fetching PR diff...", flush=True)
        diff = await get_pr_diff(client, repo, pr_number)
        print(f"[INFO] Diff size: {len(diff)} chars", flush=True)

        if len(diff.strip()) < 10:
            print("[INFO] Empty diff — nothing to review", flush=True)
            return

        # Run all 5 agents in parallel
        print("[INFO] Running 5 specialist agents in parallel...", flush=True)
        agent_tasks = [
            run_agent(client, name, cfg, diff, pr_title)
            for name, cfg in AGENT_CONFIGS.items()
        ]
        agent_results: list[dict] = await asyncio.gather(*agent_tasks)

        for r in agent_results:
            count = len(r.get("findings", []))
            print(f"[INFO] {r['agent']:12s} → {count} finding(s) | {r.get('summary', '')[:60]}", flush=True)

        # Aggregate
        print("[INFO] Aggregating results...", flush=True)
        agg = await aggregate(client, agent_results, pr_title, diff)

        # Format and post comment
        comment = format_comment(agent_results, agg)
        print(f"[INFO] Posting review comment ({len(comment)} chars)...", flush=True)
        await github_post(client, f"/repos/{repo}/issues/{pr_number}/comments", {"body": comment})
        print("[INFO] ✅ Review comment posted successfully!", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
