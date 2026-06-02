# Architecture & System Design

Reference blueprint: **PR-Agent / Qodo Merge** (Apache-2.0 OSS) — the canonical pipeline shape. We copy its staging but **trim** the abstractions for v1.

## How it hooks into the codebase

v1 = a **published GitHub Action that runs inside the user's own repo**. The user drops a workflow file and adds one secret:

```yaml
# .github/workflows/pr-review.yml
on:
  pull_request:
    types: [opened, synchronize, reopened, ready_for_review]
permissions:
  contents: read          # read the diff
  pull-requests: write    # post the review
jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: nitingupta220/review-agent@v1
        env:
          OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}   # auto-minted, scoped per run
```

**Why Action-first over a GitHub App + webhook SaaS:** we custody nothing (no webhook server, no App private key, no HMAC verification, no per-tenant key vault, no hosting bill). The OpenRouter key stays in the user's own encrypted Actions secrets. The GitHub App is the documented **v2 "managed flip"** — and because the pipeline is stateless, it's an additive change (swap the entrypoint), not a rewrite.

## Trigger matrix

Forked PRs do **not** receive repo secrets on `pull_request` (GitHub strips them by design), so v1 has two entry paths:

**Path A — same-repo PR (internal): auto-review.**
`pull_request` (opened/synchronize/reopened/ready_for_review) → run has `OPENROUTER_API_KEY` → full pipeline runs automatically. Detect via `pull_request.head.repo.full_name == base.repo.full_name` (`fork == false`).

**Path B — forked PR (external): maintainer-gated.**
The `pull_request` run fires but has no key, so it posts a one-time hint: *"🤖 A maintainer can comment `/review` to run the AI review."* A maintainer's `/review` comment fires an **`issue_comment`** event, which runs from the default branch **with base-repo secrets** (the safe, secret-bearing channel for fork PRs). That workflow:

1. Confirms it's a PR comment (`github.event.issue.pull_request` exists) and the body is `/review`.
2. **Authorizes the commenter** — only run if `author_association ∈ {OWNER, MEMBER, COLLABORATOR}` (or a `GET /repos/{o}/{r}/collaborators/{user}/permission` check for `write`+). Without this, fork authors could drain the maintainer's OpenRouter quota or use it as a free LLM proxy.
3. Fetches the PR diff **by PR number via API** — it **never checks out or executes fork code** (reading diff text is safe; executing it is the pwn-request vector).
4. Runs the identical pipeline and posts the review.

The `/review` command also serves as the manual re-trigger for any PR. Two small workflow files (`pr-review.yml`, `pr-review-command.yml`), one shared engine.

> `pull_request_target` is **banned**: it runs with the base repo's read/write token + secrets even for forks, and combined with a PR-head checkout is the classic "pwn request" RCE / secret-exfiltration vector. A read-only API approach is the safe posture.

## Internal shape (trimmed)

Keep the **staged pipeline** as pure functions with clean module boundaries; keep **OpenRouter calls in one module**. Do **not** build `GitProvider`/`ContextProvider` interfaces with a single implementation each — re-extract those when GitLab or the App actually arrives. (Clean module seams grow fine; speculative interfaces against an imaginary second caller just get refactored anyway.)

```
Entrypoint (Action)
  → Config loader   (.pr-review.yaml + .pr-review/rules.md, documented precedence)
  → Ingest          (GET /pulls/{n}/files; parse hunks; tag added/modified/deleted/binary/renamed)
  → Filter          (ignore-globs: lockfiles / *.min.* / dist / vendor / generated; strip delete-only hunks)
  → Secret scan     (gitleaks over diff → redact BEFORE anything reaches the model)
  → Contextualize   (inject reference line numbers; expand asymmetric context to fn/class boundary)
  → Compress        (token-count; fit into ONE call; clip oversized + list "not reviewed")
  → Infer           (OpenRouter: models[] fallback array, json_schema, provider.zdr)
  → Parse/repair    (validate JSON against schema; repair / single re-prompt on failure)
  → Post-process    (validate line anchors against hunks; dedup; gate; cap ~30 by severity)
  → Post            (ONE batched Review + ONE persistent summary comment; write last_reviewed_sha)
  → Telemetry       (model used, tokens, requests-used, redactions, skipped files)
```

## Components

| Component | Responsibility |
|---|---|
| **Action Entrypoint** | Reads `$GITHUB_EVENT_PATH` (PR number, repo, base/head SHA, action type) + env secrets; owns the SHA-dedup gate and the full-vs-incremental decision. Isolated so a v2 webhook entrypoint can call the same engine. |
| **Config Loader** | Loads `.pr-review.yaml` + optional `.pr-review/rules.md`; resolves precedence (PR command > repo yaml > org remote config > defaults); selects path-glob-matched rules per changed file. |
| **Diff Ingest + Filter** | Parses unified diff into per-file hunk objects; tags file status; drops binary / missing-patch files (the `/files` patch field is omitted on huge files; endpoint caps at 3000 files); applies default + user ignore-globs; strips delete-only hunks. Surfaces skipped files (never silently drops). |
| **Secret Pre-Scanner** | Runs gitleaks (stdin/regex) over the diff before prompt assembly; redacts matches to `[REDACTED_SECRET]`; emits each as a high-severity finding. Mandatory because free models may route to training-enabled providers. |
| **Contextualize** | Emits a `__new hunk__` block with **reference line numbers prepended to each new-side line** (+ `__old hunk__` if deletions); expands asymmetric context (3 before / 1 after, cap 10) to enclosing function/class. This is the #1 lever for line-number accuracy — the model returns ranges that map back exactly; never trust it to count. |
| **Chunker / Compressor** | Token-counts the extended diff; if it fits (model context − ~1500-token output buffer) sends ONE prompt. Else: group files by language, sort big-first within language, greedily pack, clip oversized at line boundaries, track a "not reviewed (too large)" list. |
| **ModelHandler (OpenRouter)** | Wraps `/api/v1/chat/completions`. Always sends a `models[]` fallback array + `require_parameters: true` + `provider.zdr` + `response_format: json_schema`. Reads `response.model` back for telemetry. Handles 429 (backoff + advance, never blind retry), 402 (negative balance), 404 (prune dead id). Fetches `/api/v1/models` at startup to set the per-model compression cap. |
| **Structured Output + Parser/Repair** | Strict JSON schema for findings; validates; on failure runs JSON-repair / one re-prompt (free providers sometimes emit JSON-as-text). |
| **Post-Process Filter** | Validates each finding's lines map to a real hunk anchor (drop if not); dedups by (file, line-range, category); gates on actionability + evidence span (**not** the self-reported confidence number); caps to ~30 by severity (GitHub silently drops past ~100/review). |
| **GitHub posting** | Builds inline `comments[]` (`path`/`line`/`side`, +`start_line`/`start_side` for multi-line) with ` ```suggestion ` blocks from `existing_code → improved_code` (matched by content, robust to line drift). Posts ONE batched `POST /pulls/{n}/reviews` (`event: COMMENT`, `commit_id = head SHA`). Upserts one persistent summary comment (hidden HTML marker) carrying `last_reviewed_sha`. |
| **Telemetry / Run Logger** | One structured record per review: model used, prompt/completion/cached tokens, est. cost, latency, requests-used, redactions, files skipped, outcome. In v1 this is run logs + a line in the summary comment; no DB. |

## End-to-end flow

1. **Trigger** on `pull_request` (Path A) or maintainer `/review` comment (Path B). Never `pull_request_target`; never execute checked-out PR code.
2. **Dedup / incremental gate.** Read head SHA. If it equals stored `last_reviewed_sha` (hidden marker in the summary comment) → no-op. On `synchronize` → review only the delta since `last_reviewed_sha`. If that SHA is unreachable after a force-push/rebase → **fall back to a full review**.
3. **Load config** with precedence.
4. **Ingest** changed files (`GET /pulls/{n}/files`, paginated at 100); handle the 3000-file cap and omitted-patch files.
5. **Filter** binary/missing-patch/ignored files; strip delete-only hunks; record skipped files.
6. **Secret pre-scan** → redact before prompt assembly (scan before clipping so secrets in oversized regions aren't missed).
7. **Contextualize**: reference-line-number injection + asymmetric context; build the per-file set of valid `(path, line, side)` anchors from `@@` headers.
8. **Compress** to fit one model call.
9. **Assemble prompt**: fence all PR content as *untrusted data, not instructions*; put the static system + custom-rules block **first** (prompt-cache friendliness); include the strict JSON schema and anti-false-positive directives ("review only `+` lines", "discrete actionable issues only", "no speculation about unseen code paths", "prefer not reporting over guessing").
10. **Infer**: ONE OpenRouter call with `models[]` fallback, `require_parameters: true`, `provider.zdr` per the privacy decision, `response_format: json_schema`. 429 → backoff + advance; 402 → "add credit"; 404 → prune dead id.
11. **Parse/repair** JSON; one re-prompt on failure.
12. **Post-process**: anchor-validate, dedup, gate, cap.
13. **Build outputs**: inline comments + ` ```suggestion ` blocks; summary with walkthrough table, severity counts, skipped-files note, "requests used".
14. **Post once**: batched review (`event: COMMENT`, never auto-approve/request-changes in v1), `commit_id = head SHA`; upsert the persistent summary comment and write the new `last_reviewed_sha`. Add a "head moved during run → abort, let next synchronize handle it" check to avoid a stale-anchor 422.
15. **Log** the telemetry record.
