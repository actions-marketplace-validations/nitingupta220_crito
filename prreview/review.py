"""Orchestration entrypoint for the PR review agent.

This is the single integration module that wires every builder module together
into one read-the-diff -> post-one-review GitHub Action. It is the ONLY module
that imports both the httpx-backed clients (``openrouter``, ``github_client``)
and the stdlib-only helpers, so it is deliberately NOT imported by the
stdlib-only smoke test.

Design invariants (must hold):
  * Pure read-and-comment. We NEVER check out, build, merge, or execute PR /
    fork code. The diff is read through the GitHub API only.
  * ``pull_request`` and ``issue_comment`` triggers only — NEVER
    ``pull_request_target`` (that is enforced at the workflow layer; here we
    simply never touch the head tree).
  * Secrets are redacted BEFORE any text reaches a model.
  * All PR-author-controlled text is fenced as untrusted in the prompt, and all
    model-authored text is sanitized before it is posted.
  * One batched review per run; a sticky summary comment carries the
    last-reviewed SHA so re-runs on the same head are skipped.

Flow (see ``run``):
  Path A  pull_request event  -> review if same-repo or a key is present;
          fork PR with no key  -> post the hint comment and exit 0.
  Path B  issue_comment "/review" on a PR -> authorize the commenter, then run.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import traceback

from prreview import authz, diff, ensemble, postprocess, sanitize, secrets_scan
from prreview.config import load_config
from prreview.github_client import GitHubClient
from prreview.openrouter import OpenRouterClient
from prreview.prompts import SYSTEM_PROMPT, build_user_prompt
from prreview.schema import FINDINGS_SCHEMA

# Hidden HTML-comment sentinel embedded in the sticky summary comment. It both
# identifies "our" comment (so upsert edits in place) and carries the last
# reviewed head SHA so we can skip a no-op re-review.
_SUMMARY_MARKER = "<!-- prreview:summary -->"
_SHA_MARKER_PREFIX = "<!-- prreview:last_reviewed_sha="
_SHA_MARKER_SUFFIX = " -->"


# ---------------------------------------------------------------------------
# Event / environment parsing helpers
# ---------------------------------------------------------------------------


def _load_event(env: dict) -> dict:
    """Load the GitHub event payload from $GITHUB_EVENT_PATH (best effort)."""
    path = env.get("GITHUB_EVENT_PATH")
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _owner_repo(event: dict, env: dict) -> tuple:
    """Resolve (owner, repo) from the event, falling back to $GITHUB_REPOSITORY."""
    repo_obj = event.get("repository")
    if isinstance(repo_obj, dict):
        full = repo_obj.get("full_name")
        if isinstance(full, str) and "/" in full:
            owner, repo = full.split("/", 1)
            return owner, repo
        owner_obj = repo_obj.get("owner")
        name = repo_obj.get("name")
        if isinstance(owner_obj, dict) and isinstance(name, str):
            login = owner_obj.get("login")
            if isinstance(login, str):
                return login, name
    full = env.get("GITHUB_REPOSITORY", "")
    if isinstance(full, str) and "/" in full:
        owner, repo = full.split("/", 1)
        return owner, repo
    return "", ""


def _event_name(env: dict) -> str:
    """The trigger name: 'pull_request', 'issue_comment', etc."""
    return env.get("GITHUB_EVENT_NAME") or ""


def _pr_number(event: dict, event_name: str) -> int:
    """Extract the PR number for either a pull_request or issue_comment event."""
    if event_name == "pull_request":
        pr = event.get("pull_request")
        if isinstance(pr, dict) and isinstance(pr.get("number"), int):
            return pr["number"]
        num = event.get("number")
        if isinstance(num, int):
            return num
    # issue_comment on a PR: the PR number is the issue number.
    issue = event.get("issue")
    if isinstance(issue, dict) and isinstance(issue.get("number"), int):
        return issue["number"]
    return 0


def _is_fork_pr(pr_obj: dict) -> bool:
    """True when the PR head repo differs from the base repo (a fork PR).

    Same-repo branches share a repo id; a fork PR's head.repo is a different
    repo (or absent, when the fork was deleted). Either case is treated as a
    fork for the purpose of secret exposure / authz.
    """
    if not isinstance(pr_obj, dict):
        return False
    head = pr_obj.get("head") or {}
    base = pr_obj.get("base") or {}
    head_repo = head.get("repo") if isinstance(head, dict) else None
    base_repo = base.get("repo") if isinstance(base, dict) else None
    if not isinstance(head_repo, dict):
        # Head repo deleted/unavailable -> treat as fork (fail safe).
        return True
    if not isinstance(base_repo, dict):
        return False
    head_id = head_repo.get("id")
    base_id = base_repo.get("id")
    if head_id is not None and base_id is not None:
        return head_id != base_id
    # Fall back to full_name comparison when ids are missing.
    return head_repo.get("full_name") != base_repo.get("full_name")


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------


def _telemetry(**fields) -> None:
    """Print one structured (JSON) telemetry line. No secrets are ever included."""
    try:
        print("prreview " + json.dumps(fields, default=str, sort_keys=True))
    except Exception:
        # Telemetry must never crash the run.
        print(f"prreview {fields}")


# ---------------------------------------------------------------------------
# Summary / SHA marker helpers
# ---------------------------------------------------------------------------


def _extract_last_sha(comment_body: str) -> str:
    """Pull the stored last_reviewed_sha out of an existing summary comment."""
    if not comment_body:
        return ""
    start = comment_body.find(_SHA_MARKER_PREFIX)
    if start == -1:
        return ""
    start += len(_SHA_MARKER_PREFIX)
    end = comment_body.find(_SHA_MARKER_SUFFIX, start)
    if end == -1:
        return ""
    return comment_body[start:end].strip()


def _find_existing_summary(gh: GitHubClient, pr: int) -> str:
    """Return the body of our existing sticky summary comment, or '' if none.

    Reuses the client's private marker scan so we do not duplicate pagination
    logic. Failures degrade to '' (we simply lose incremental-skip and re-review
    the whole diff, which is safe).
    """
    try:
        comment_id = gh._find_comment_by_marker(pr, _SUMMARY_MARKER)
    except Exception:
        return ""
    if comment_id is None:
        return ""
    try:
        resp = gh._client.get(
            gh._repo_path(f"/issues/comments/{comment_id}")
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return ""
    return data.get("body") or ""


def _build_summary_body(findings: list, head_sha: str, served_models: list,
                        skipped: list, secret_count: int) -> str:
    """Render the sticky summary comment body, embedding the hidden markers."""
    n = len(findings)
    sev_counts: dict = {}
    for f in findings:
        sev = f.get("severity", "?")
        sev_counts[sev] = sev_counts.get(sev, 0) + 1

    if n == 0 and secret_count == 0:
        headline = "No issues found in the changed lines. ✅"
    else:
        bits = []
        for sev in ("critical", "major", "minor", "nit"):
            if sev_counts.get(sev):
                bits.append(f"{sev_counts[sev]} {sev}")
        summary_bits = ", ".join(bits) if bits else f"{n} finding(s)"
        headline = f"Reviewed the diff and found {n} issue(s): {summary_bits}."

    lines = [
        "## PR Review",
        "",
        headline,
    ]
    if secret_count:
        lines.append("")
        lines.append(
            f"⚠️ {secret_count} potential hardcoded secret(s) were detected and "
            "redacted before review. Rotate any leaked credentials."
        )
    if skipped:
        shown = ", ".join(skipped[:10])
        more = "" if len(skipped) <= 10 else f" (+{len(skipped) - 10} more)"
        lines.append("")
        lines.append(f"Skipped (not reviewed): {shown}{more}")
    if served_models:
        lines.append("")
        lines.append(f"_Models: {', '.join(served_models)}_")

    return "\n".join(lines)


def _summary_markers(head_sha: str) -> str:
    """The hidden HTML-comment marker block (identity + last-reviewed SHA).

    Appended to the summary AFTER sanitization: ``sanitize_comment`` strips HTML
    comments, so baking these into the pre-sanitized body silently deletes them —
    which breaks incremental-skip and leaves a duplicate summary comment on every
    run. Keeping them out of the sanitized text preserves the sticky marker.
    """
    return f"{_SUMMARY_MARKER}\n{_SHA_MARKER_PREFIX}{head_sha}{_SHA_MARKER_SUFFIX}"


def _render_summary(findings: list, head_sha: str, served_models: list,
                    skipped: list, secret_count: int) -> str:
    """Build the visible summary, sanitize it, then append the hidden markers."""
    visible = _build_summary_body(
        findings, head_sha, served_models, skipped, secret_count
    )
    return sanitize.sanitize_comment(visible) + "\n\n" + _summary_markers(head_sha)


# ---------------------------------------------------------------------------
# Secret-finding anchoring
# ---------------------------------------------------------------------------


def _anchor_secret_findings(secret_findings: list, filename: str,
                            valid_anchors: set) -> list:
    """Re-home raw secret findings onto a real new-side anchor for ``filename``.

    ``secrets_scan.redact`` computes line numbers against the *patch* text (which
    includes diff markers), so its line numbers do not line up with the
    new-file-side ``valid_anchors`` the renderer produced. To make sure a
    genuine leaked credential is never silently dropped by the downstream anchor
    gate, we stamp the file name and snap each finding to the lowest real
    new-side anchor for that file. Findings for a file with no surviving anchor
    (e.g. the file was clipped out of the budget) are returned unchanged so the
    caller can still surface them in the summary.
    """
    file_anchors = sorted(ln for (p, ln) in valid_anchors if p == filename)
    out = []
    for f in secret_findings:
        g = dict(f)
        g["relevant_file"] = filename
        if file_anchors:
            g["start_line"] = file_anchors[0]
            g["end_line"] = file_anchors[0]
        out.append(g)
    return out


# ---------------------------------------------------------------------------
# Authorization (Path B)
# ---------------------------------------------------------------------------


def _commenter_login(event: dict) -> str:
    comment = event.get("comment") or {}
    user = comment.get("user") if isinstance(comment, dict) else None
    if isinstance(user, dict):
        login = user.get("login")
        if isinstance(login, str):
            return login
    return ""


def _commenter_association(event: dict) -> str:
    comment = event.get("comment") or {}
    if isinstance(comment, dict):
        assoc = comment.get("author_association")
        if isinstance(assoc, str):
            return assoc
    return ""


# ---------------------------------------------------------------------------
# Main async flow
# ---------------------------------------------------------------------------


async def run(event: dict, env: dict) -> int:
    """Run one review pass. Returns a process exit code (0 = ok)."""
    started = time.monotonic()
    gh = None
    try:
        event_name = _event_name(env)
        owner, repo = _owner_repo(event, env)
        pr_number = _pr_number(event, event_name)
        github_token = env.get("GITHUB_TOKEN") or env.get("INPUT_GITHUB_TOKEN") or ""
        openrouter_key = env.get("OPENROUTER_API_KEY") or ""

        if not owner or not repo or not pr_number:
            _telemetry(event="skip", reason="no_pr_in_event",
                       event_name=event_name)
            return 0
        if not github_token:
            _telemetry(event="error", reason="missing_github_token")
            return 1

        api_base = env.get("GITHUB_API_URL") or "https://api.github.com"
        gh = GitHubClient(github_token, owner, repo, base_url=api_base)

        # ── Trigger routing ────────────────────────────────────────────────
        pr_obj = gh.get_pr(pr_number)
        fork = _is_fork_pr(pr_obj)

        if event_name == "issue_comment":
            # Path B: only act on a "/review" command on a PR.
            comment = event.get("comment") or {}
            body = (comment.get("body") or "").strip() if isinstance(comment, dict) else ""
            if not body.startswith("/review"):
                _telemetry(event="skip", reason="not_a_review_command",
                           pr=pr_number)
                return 0
            login = _commenter_login(event)
            assoc = _commenter_association(event)
            permission = "none"
            if login:
                permission = gh.get_permission(login)
            if not authz.is_authorized(assoc, permission):
                _telemetry(event="skip", reason="unauthorized_commenter",
                           pr=pr_number, association=assoc, permission=permission)
                # Terse, non-pinging note. Do NOT spend any model quota.
                try:
                    gh.post_hint_comment(
                        pr_number,
                        "Sorry, only repository collaborators (write access or "
                        "above) can trigger `/review`.",
                    )
                except Exception:
                    pass
                return 0
        elif event_name == "pull_request":
            # Path A: a fork PR with no OpenRouter key cannot be reviewed inline
            # (the key is not exposed to fork-triggered runs). Leave a hint that
            # explains the `/review` command path, then exit cleanly.
            if fork and not openrouter_key:
                _telemetry(event="hint", reason="fork_pr_no_key", pr=pr_number)
                try:
                    gh.post_hint_comment(
                        pr_number,
                        "This pull request comes from a fork, so the automated "
                        "review did not run (the API key is not exposed to fork "
                        "builds for security). A maintainer can trigger a review "
                        "by commenting `/review` on this PR.",
                    )
                except Exception:
                    pass
                return 0
        # Any other event name: fall through and attempt a review if we can.

        if not openrouter_key:
            _telemetry(event="skip", reason="missing_openrouter_key",
                       pr=pr_number)
            return 0

        # ── Config ─────────────────────────────────────────────────────────
        repo_root = env.get("GITHUB_WORKSPACE") or os.getcwd()
        cfg = load_config(repo_root)

        head_sha = ""
        head = pr_obj.get("head") if isinstance(pr_obj, dict) else None
        if isinstance(head, dict):
            head_sha = head.get("sha") or ""

        pr_title = pr_obj.get("title") or ""
        pr_body = pr_obj.get("body") or ""

        # ── Incremental skip via sticky summary comment ────────────────────
        existing_summary = _find_existing_summary(gh, pr_number)
        last_sha = _extract_last_sha(existing_summary)
        if head_sha and last_sha and head_sha == last_sha:
            _telemetry(event="skip", reason="already_reviewed_head",
                       pr=pr_number, head_sha=head_sha)
            return 0

        # ── Fetch + filter files ───────────────────────────────────────────
        files = gh.get_pr_files(pr_number)
        kept, skipped = diff.filter_files(files)
        if cfg.max_files and len(kept) > cfg.max_files:
            skipped.extend(
                f.get("filename") or f.get("path") or "?"
                for f in kept[cfg.max_files:]
            )
            kept = kept[: cfg.max_files]

        # ── Redact secrets BEFORE anything reaches a model ─────────────────
        secret_findings: list = []
        for f in kept:
            patch = f.get("patch") or ""
            if not patch:
                continue
            redacted_patch, file_secrets = secrets_scan.redact(patch)
            if file_secrets:
                f["patch"] = redacted_patch
                filename = f.get("filename") or f.get("path") or ""
                secret_findings.extend(
                    {"_file": filename, "_raw": s} for s in file_secrets
                )

        # ── Render the (now-redacted) diff with reference line numbers ─────
        rendered_diff, valid_anchors, too_large = diff.parse_and_render(
            kept, cfg.max_diff_chars
        )
        if too_large:
            skipped.extend(too_large)

        # Now that valid_anchors exist, snap secret findings onto real anchors.
        anchored_secret_findings: list = []
        unanchored_secret_count = 0
        # Group raw secret findings by file so we re-home them correctly.
        by_file: dict = {}
        for item in secret_findings:
            by_file.setdefault(item["_file"], []).append(item["_raw"])
        for filename, raws in by_file.items():
            homed = _anchor_secret_findings(raws, filename, valid_anchors)
            for h in homed:
                if (h["relevant_file"], h["start_line"]) in valid_anchors:
                    anchored_secret_findings.append(h)
                else:
                    unanchored_secret_count += 1

        if not rendered_diff.strip() and not anchored_secret_findings:
            # Nothing reviewable (all binary/ignored/deletions). Record the SHA so
            # we don't keep re-fetching, and exit cleanly.
            try:
                gh.upsert_summary_comment(
                    pr_number,
                    _render_summary([], head_sha, [], skipped, len(secret_findings)),
                    _SUMMARY_MARKER,
                )
            except Exception:
                pass
            _telemetry(event="done", reason="no_reviewable_diff", pr=pr_number,
                       skipped=len(skipped))
            return 0

        # ── Build prompts (untrusted PR content is fenced inside the user msg) ─
        user_prompt = build_user_prompt(
            rendered_diff=rendered_diff,
            pr_title=pr_title,
            pr_body=pr_body,
            custom_rules=cfg.custom_rules,
        )

        # ── Ensemble: same prompt -> <=3 models -> union + dedup ───────────
        client = OpenRouterClient(openrouter_key, cfg.models)
        model_findings = await ensemble.run_ensemble(
            client=client,
            models=cfg.models,
            system=SYSTEM_PROMPT,
            user=user_prompt,
            response_format=FINDINGS_SCHEMA,
        )

        served_models: list = []
        for f in model_findings:
            for m in f.get("models") or []:
                if m and m not in served_models:
                    served_models.append(m)

        # ── Merge secret findings, then finalize against valid anchors ─────
        all_findings = list(model_findings) + anchored_secret_findings
        finalized = postprocess.finalize(
            all_findings, valid_anchors, max_findings=cfg.max_findings
        )

        # ── Sanitize every model-authored comment before posting ───────────
        inline_comments: list = []
        for f in finalized:
            safe_comment = sanitize.sanitize_comment(f.get("comment") or "")
            if not safe_comment.strip():
                continue
            improved = f.get("improved_code")
            sev = f.get("severity", "")
            cat = f.get("category", "")
            header = f"**[{sev} · {cat}]** " if sev or cat else ""
            body_md = header + safe_comment
            if improved:
                safe_improved = sanitize.sanitize_comment(improved)
                if safe_improved.strip():
                    body_md += f"\n\n```suggestion\n{safe_improved}\n```"
            inline_comments.append(
                {
                    "relevant_file": f.get("relevant_file"),
                    "start_line": f.get("start_line"),
                    "end_line": f.get("end_line"),
                    "comment": body_md,
                }
            )

        # ── Summary comment (also sanitized) ───────────────────────────────
        total_secret_count = len(anchored_secret_findings) + unanchored_secret_count
        safe_summary = _render_summary(
            finalized, head_sha, served_models, skipped, total_secret_count
        )

        # ── Post: ONE batched review + sticky summary ──────────────────────
        review_posted = False
        if inline_comments:
            try:
                gh.post_review(pr_number, head_sha, safe_summary, inline_comments)
                review_posted = True
            except Exception as exc:
                # The reviews endpoint is atomic: one bad anchor rejects ALL of
                # it (422). Surface GitHub's specific reason, then fall back to
                # posting each inline comment independently so the valid ones
                # still land instead of losing the whole review to one bad anchor.
                detail = ""
                resp = getattr(exc, "response", None)
                if resp is not None:
                    try:
                        detail = resp.text[:600]
                    except Exception:
                        detail = ""
                fallback_posted = 0
                for c in inline_comments:
                    if gh.post_inline_comment(pr_number, head_sha, c):
                        fallback_posted += 1
                if fallback_posted:
                    review_posted = True
                _telemetry(event="warn", reason="batched_review_failed",
                           pr=pr_number, error=type(exc).__name__, detail=detail,
                           fallback_posted=fallback_posted)
        if not review_posted:
            gh.upsert_summary_comment(pr_number, safe_summary, _SUMMARY_MARKER)
        else:
            # Keep the sticky summary (with the SHA marker) up to date too, so the
            # incremental-skip works on the next run.
            try:
                gh.upsert_summary_comment(pr_number, safe_summary, _SUMMARY_MARKER)
            except Exception:
                pass

        latency = round(time.monotonic() - started, 2)
        _telemetry(
            event="done",
            pr=pr_number,
            event_name=event_name,
            fork=fork,
            served_models=served_models,
            model_findings=len(model_findings),
            secret_findings=total_secret_count,
            posted_findings=len(inline_comments),
            skipped_files=len(skipped),
            valid_anchors=len(valid_anchors),
            latency_s=latency,
        )
        return 0

    except Exception as exc:  # noqa: BLE001 — single top-level safety net
        latency = round(time.monotonic() - started, 2)
        _telemetry(
            event="error",
            error=type(exc).__name__,
            message=str(exc),
            latency_s=latency,
        )
        traceback.print_exc()
        return 1
    finally:
        if gh is not None:
            try:
                gh.close()
            except Exception:
                pass


def main() -> int:
    """Synchronous entrypoint: load the event + env, then run the async flow."""
    env = dict(os.environ)
    event = _load_event(env)
    return asyncio.run(run(event, env))


if __name__ == "__main__":
    sys.exit(main())
