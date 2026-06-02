# Decisions, Open Questions & Risks

## Direction decisions (soft — subject to revision)

### 1. Target repos: OSS too, maintainer-gated
Same-repo PRs auto-review on `pull_request`. Forked PRs get **no secrets**, so they are reviewed only when a trusted maintainer comments `/review` (an `issue_comment`-triggered, authorized, read-only flow). Forced by GitHub stripping secrets from fork-triggered runs; `pull_request_target` is banned (pwn-request RCE). UX is "maintainer-initiated" for external contributors, not automatic — state this plainly in docs.

### 2. Privacy: ZDR default + opt-in community mode
- **Default**: every request sends `provider.zdr: true` + account training-opt-out. Safe for proprietary diffs.
- **Community mode**: a `privacy.mode: community` flag drops ZDR to widen the free pool, behind an explicit, disclosed opt-in (good for public/hobby repos).
- **Empty-route behavior**: if ZDR-strict returns zero eligible free providers → **fail loudly** (`"No zero-data-retention free provider is currently available. Enable community mode or add a paid/BYOK provider."`) — never silently fall through to a training-enabled provider.

This means the "free + private" combination may sometimes have no available free model. That is inherent to OpenRouter's free tier, not a design flaw.

### 3. Distribution: GitHub Action first
Self-hosted Action; user custodies the key; we run zero infra. The GitHub App + webhook SaaS is the **v2 "managed flip"** — additive because the pipeline is stateless.

---

## Things cut from v1 (from the adversarial critique)

The first-draft design was "a 3-month build wearing a v1 costume." Cuts that keep it ~4–6 weeks:

- **Cut the self-reflection / re-scoring pass.** It doubles the scarcest resource (requests) to chase false positives that chill-profile + severity gating + anti-speculation prompt + line-anchor validation already mostly suppress. Add it back only if FP complaints materialize.
- **Cut tree-sitter / ctags symbol extraction.** Language-specific binaries and parse failures for marginal gain; the reference-line-number trick needs none of it.
- **Drop per-file map-reduce fallback.** With 1M-context lead models + clipping, true overflow is vanishingly rare; if you overflow, clip harder and list "not reviewed."
- **Collapse the formal interfaces** into concrete modules (keep the staged pipeline).
- **Drop the "suggest a rule on dismissal" learnings teaser** — detecting dismissals needs an event stream a stateless Action doesn't have; that's a managed-mode feature.
- **Reduce the model fallback array** from 5 to ~3.

---

## Open questions to resolve before/while building

1. **Quota math (load-bearing).** Does a `models[]` fallback **share** or **multiply** the per-account daily free quota? **Partially settled by the 2026-06-02 spike** ([`spike-openrouter-quota.md`](spike-openrouter-quota.md)): the array is hard-capped at **3** (worst case 3×, not 5×); the free daily counter is **not observable** via any endpoint or header, so a definitive share-vs-multiply answer needs a one-time daily-cap exhaustion run (~50 requests on a $0 key, resets midnight UTC) — pending owner go-ahead. Mitigation is the same regardless: minimize calls/PR, lead with the most-reliable model, treat every request as potentially counting.
2. **Concurrency across simultaneous PRs sharing one key.** GitHub Actions runs jobs in parallel across PRs; the 20 RPM / 50–1000 per-day caps are per **account**, not per repo. Five PRs in one minute = five concurrent runs hitting the same key. Decide: a `concurrency:` group in the template, a client-side semaphore, or "expect throttling, lean on backoff."
3. **Positioning honesty.** Lead with "$0" or "effectively a one-time $10 OpenRouter credit to be usable"? The 50/day free tier almost certainly can't sustain one active repo.
4. **Weekly token allocations.** Per-model weekly token buckets (e.g. DeepSeek V4 Flash ~112M/wk, Kimi K2.6 ~15.9B/wk) are orthogonal to RPM/RPD and unmodeled. A few large reviews can exhaust a weekly bucket mid-week. Detect "throttled by weekly cap" vs ordinary 429.
5. **Container vs JS action.** Docker action (slow cold start, heavier, the user pays Actions minutes) vs composite/JS action (faster, reimplements gitleaks/diff parsing). Affects per-PR latency, the consumer's Actions-minutes bill, and language choice.
6. **Incremental fallback rule.** When `last_reviewed_sha` is unreachable after force-push/rebase: full review (chosen) vs three-dot diff against merge-base.

---

## Top risks (and mitigations)

| Risk | Mitigation |
|---|---|
| **Free-tier rate limits** (20 rpm; 50/day under $10, 1000/day after; failed calls count) | One call/PR via compression; `models[]` fallback; backoff (never blind retry); SHA-dedup + incremental review; onboarding pushes the one-time $10 credit. |
| **Fragile diff anchoring** (atomic 422 if any comment is out-of-hunk; line shift on force-push) | Hunk-line index; reference-line-number injection; hard-validate every anchor before posting; recompute each incremental run; `commit_id = head SHA`; abort if head moved mid-run. |
| **Uneven structured-output support** on free models | `json_schema` + `require_parameters: true`; defensive JSON-repair parser; one re-prompt; simple schema. |
| **`:free` roster churn** (deprecations, renames, paid migrations) | Model list as runtime config; fetch `/api/v1/models` for context; 404-prune dead ids; multi-model fallback. |
| **Privacy / training exposure** of proprietary diffs | `provider.zdr` default; explicit disclosure; secret pre-scan + redaction; community-mode opt-in; fail-loud on empty ZDR route. |
| **Nit-overload / false positives** (#1 reason teams disable AI reviewers) | Chill default profile; severity gating; anti-speculation directives; "prefer not reporting over guessing"; confidence used only as tiebreak. |
| **Prompt injection via PR title/body/diff** (the vector that hijacked Claude Code Security Review, Gemini CLI Action, Copilot agent) | Architectural: agent is pure read-and-comment with ZERO write/merge/exec; untrusted-content fencing; schema-bound output (no free-text "approve" can escape); never `pull_request_target`; never execute checked-out PR code. |
| **Over-scoping into the moat** (graph/embeddings, swarms, 40+ linters) | v1 is diff + light context + NL path rules only; RAG and hybrid linters deferred. |
