# Research Findings

Raw research across six dimensions that informed the architecture. Conducted 2026-06-02 via a parallel multi-agent web-research sweep. Sources are listed per section.

---

## 1. Competitive landscape & reference architectures

**Summary.** The market converged on one canonical pipeline: webhook/Action trigger → fetch PR diff → compress diff to fit token budget → (optional) build codebase context → run LLM with structured (YAML/JSON) output → self-reflect/score → filter → post one batched review with inline comments + a summary. **PR-Agent / Qodo (Apache-2.0)** is the best blueprint: a 4-layer architecture (UI / orchestrator / tools / git-provider abstraction), slash commands (`/review`, `/describe`, `/improve`, `/ask`), LiteLLM for multi-model, an explicit PR-compression algorithm (cap ~32k tokens), and a self-reflection scoring pass (0–10) that filters suggestions.

**Key findings.**
- **PR-Agent layers:** (1) UI (CLI / Action / webhook / polling); (2) Orchestrator — `handle_request()` applies repo settings → validates args → `command2class` dict lookup → instantiate+run tool; (3) Tools — `PRReviewer` (`/review`), `PRDescription` (`/describe`), `PRCodeSuggestions` (`/improve`), `PRQuestions` (`/ask`); (4) Git-provider abstraction (GitHub/GitLab/Bitbucket/Azure/Gitea). Prompts are Jinja2 templates with strict YAML output.
- **Table-stakes for v1:** PR summary, inline line-level suggestions with severity, file walkthrough table, incremental review on push, config file with NL path rules.
- **Defer:** codebase-graph indexing, sequence diagrams, chat-replies, auto-fix-commit, multi-agent swarm.
- **Pricing wedge:** CodeRabbit $12–24, Qodo ~$19, Graphite $20–40, Bito $15–25 per seat/mo — all running the same pipeline shape we can run on free models via BYOK.
- **Posting:** one batched `POST /pulls/{n}/reviews` with `comments[]`, plus one persistent (edited-in-place) summary comment.

**Pitfalls.** Free-model rate limits dictate batching; the `:free` roster churns; structured-output support is uneven; diff position mapping is fragile; posting many separate comments spams + hits secondary limits; nit-overload is the #1 reason teams disable AI reviewers.

**Sources.** github.com/qodo-ai/pr-agent · deepwiki.com/qodo-ai/pr-agent · qodo-merge-docs.qodo.ai · docs.coderabbit.ai · greptile.com/agent · graphite.com/blog/introducing-graphite-agent-and-pricing · bito.ai/pricing · docs.github.com/copilot/concepts/agents/code-review · danger.systems/js · docs.github.com/rest/pulls/reviews

---

## 2. OpenRouter free coding/reasoning models (mid-2026)

**Summary.** Best free defaults for code review: `qwen/qwen3-coder:free` (1M ctx, strongest free coder), `deepseek/deepseek-v4-flash:free` (1M ctx, native reasoning), `moonshotai/kimi-k2.6:free` (262K ctx), `z-ai/glm-4.5-air:free` (131K ctx, hybrid thinking), `openai/gpt-oss-120b:free` (131K, reliable structured output). Free tier: **20 req/min**, **50 req/day** (<$10 credit) → **1000/day** after a one-time $10 purchase. The two API features to build on: the `models[]` fallback array and `response_format: json_schema`.

**Key findings.**
- GLM-4.6/4.7 and DeepSeek R1/V3 named in older guides are now **paid-only or aged out**; only free GLM is 4.5-Air; Kimi is **K2.6**, not K2. → treat IDs as runtime data.
- **`models[]` fallback** auto-triggers on 429 / context-length error / moderation / downtime; the served model is returned in `response.model` (billing follows it). Deprecated models → 404.
- **Structured outputs**: `response_format:{type:'json_schema', strict:true,...}`; officially supported on OpenAI/Gemini/Anthropic/"most open-source"/Fireworks; **not guaranteed per-model** for Qwen/DeepSeek/Kimi/GLM → combine with `require_parameters:true` and keep a JSON-repair fallback.
- **Privacy blocker**: most free providers require the account "allow training on your data" toggle ON. ZDR / training-opt-out removes most free models from routing.
- **Variants**: `:nitro` (throughput), `:floor` (price). Prompt caching is automatic for DeepSeek, supported on Kimi K2 family + Gemini; ~3–5 min TTL; cached tokens billed ~0.25×.
- `openrouter/free` is a managed router (launched Feb 2026) that auto-picks from the free pool.

**Pitfalls.** Free models deprecate/reroute without notice (404); failed requests count against the daily cap so naive retry self-DoSes quota; structured-output support uneven; free+paid share compute so 429s occur even under your own limit; per-model **weekly** token allocations are a separate ceiling.

**Sources.** openrouter.ai/collections/free-models · openrouter.ai/docs/api/reference/limits · openrouter.ai/docs/guides/features/structured-outputs · openrouter.ai/docs/guides/routing/model-fallbacks · openrouter.ai/docs/guides/routing/provider-selection · openrouter.ai/docs/guides/privacy/data-collection · openrouter.ai/docs/guides/features/zdr · openrouter.ai/docs/guides/overview/auth/byok · openrouter.ai/docs/guides/best-practices/prompt-caching · costgoat.com/pricing/openrouter-free-models

---

## 3. GitHub integration mechanics

**Summary.** For a BYOK product the **GitHub Action** model is best for v1: `GITHUB_TOKEN` is auto-minted per run, the OpenRouter key lives in the user's repo Actions secrets (we never see it). A GitHub App + webhooks is the better long-term SaaS path but forces a webhook server, App private-key storage, and server-side custody/proxy of each user's key — defer to v2. Posting a review is one call: `POST /repos/{owner}/{repo}/pulls/{n}/reviews` with `event=COMMENT` and a `comments[]` array using `path` + `line` + `side` (not the deprecated `position`). The dominant bug is the **422 "line must be part of the diff"** — every inline comment line must land inside a parsed diff hunk or the whole atomic review call fails.

**Key findings.**
- **Action auth**: `permissions: { contents: read, pull-requests: write, checks: write }`; runner injects scoped `GITHUB_TOKEN`; OpenRouter key as `${{ secrets.OPENROUTER_API_KEY }}`. No App registration, no OAuth, no key custody.
- **Triggers**: `on: pull_request: types: [opened, synchronize, reopened, ready_for_review]`; `issue_comment` / `pull_request_review_comment` for `/review` re-trigger. **Avoid `pull_request_target`** (pwn-request). As of 2025-12-08 `pull_request_target` always loads the workflow + ref from the default branch.
- **Fetch diff**: `GET /pulls/{n}` with `Accept: application/vnd.github.diff` for raw diff; `GET /pulls/{n}/files` (paginate `per_page=100`) for `{filename, status, patch, sha, ...}`. **Caps at 3000 files**; `patch` omitted for very large files.
- **Post**: `POST /pulls/{n}/reviews` with `commit_id`, `body`, `event`, `comments[]` of `{path, body, line, side, start_line, start_side}`. Use `line`+`side` (RIGHT=additions/context, LEFT=deletions). Summary via `POST /issues/{n}/comments`. `persistent_comment` pattern edits one comment in place.
- **Checks API** (`POST /check-runs`, `checks:write`) gives a pass/fail status with up to **50 annotations** per call (appended, batch in 50s); annotations **don't** require the line to be in the diff — useful escape hatch.
- **Rate limits**: installation token 5,000/hr (scaling to 12,500); secondary limit on rapid content creation; batch into one review.
- 2026 installation-token format change: `ghs_APPID_JWT`, longer than 40 chars — treat tokens as opaque variable-length (App path only).
- **Webhook signatures** (App only): verify `X-Hub-Signature-256` HMAC over the raw body with constant-time compare; dedupe on `X-GitHub-Delivery`.
- **Large PRs / lockfiles / generated files** must be filtered before hitting OpenRouter.

**Pitfalls.** 422 is atomic (one bad comment fails all); no API to add comments to a pending review (must submit in one shot); `position` is fragile/legacy; multi-line `start_line` must be in the same hunk; `/files` caps at 3000 and omits `patch` on big files; `pull_request_target` + checkout = RCE; secondary rate limit on bursty posting; `synchronize` fires on every push.

**Sources.** docs.github.com/rest/pulls/reviews · docs.github.com/rest/pulls/pulls · docs.github.com/rest/pulls/comments · docs.github.com/webhooks/webhook-events-and-payloads · docs.github.com/actions/.../events-that-trigger-workflows · docs.github.com/apps/.../generating-an-installation-access-token · docs.github.com/rest/checks/runs · securitylab.github.com/resources/github-actions-preventing-pwn-requests · github.com/qodo-ai/pr-agent/issues/592 · github.com/orgs/community/discussions/168380

---

## 4. Core review pipeline engineering

**Summary.** Treat review as a **compression problem, not a RAG problem**: parse the unified diff into per-file hunks, reformat each into `__new hunk__` (with prepended reference line numbers) + `__old hunk__` blocks, expand a few context lines, filter noise, then greedily pack files into one prompt ranked by language and token weight, clipping overflow. For v1 on free models a **single-call whole-PR review** is the right default; per-file map-reduce is the overflow fallback only. Force structured findings, derive valid line numbers from the injected reference numbers (never from the model counting), and win quality at post-processing (confidence-threshold gating, "report nothing over guessing", dedup, incremental review). Skip embeddings/RAG entirely in v1.

**Key findings.**
- **PR-Agent compression** (`pr_processing.py`): build extended diff (`patch_extra_lines_before/after`, cap `MAX_EXTRA_LINES=10`); if over budget → `pr_generate_compressed_diff()`: sort files by language → sort big-first within language → greedily pack → strip delete-only hunks → `clip_tokens()` at line boundaries. Reserve `OUTPUT_BUFFER_TOKENS_SOFT=1500 / HARD=1000` for the response.
- **`__new hunk__` / `__old hunk__` with injected line numbers** is *the* trick for valid line numbers — the prompt tells the model the numbers are reference-only; the model returns `start_line`/`end_line` mapping back to real lines.
- **Schema fields that ship** (PR-Agent): review emits `key_issues_to_review[]` of `{relevant_file, issue_header, issue_content, start_line, end_line}` + `security_concerns`, `score`, `relevant_tests`, `estimated_effort_to_review`. Suggestions emit `{relevant_file, language, existing_code, suggestion_content, improved_code, one_sentence_summary, label, score(0-10)}`. `improved_code` matched by **content**, robust to line drift.
- **Anti-false-positive prompt directives** (proven wording): "review only `+` lines"; "each issue must be discrete and actionable"; "do not speculate that a change might break other code unless you can identify the specific affected path"; "prefer not reporting over guessing." LLM self-reported confidence is ~useless (0.99 on gibberish) → gate on actionability + evidence span, use confidence only as tiebreak.
- **Single-call vs map-reduce**: map-reduce loses cross-file reasoning (the Map phase can't see distant code) and multiplies request count → default to one call; fan out only on overflow.
- **Incremental review + noise filtering are mandatory**: store `last_reviewed_sha`, diff only new commits; default blocked-path globs; strip delete-only hunks; don't re-comment resolved threads. GitHub caps ~100 comments/review.
- **RAG not worth it for v1**: CodeRabbit's GraphRAG + vector DB is a mature-product investment; diff + light context covers most correctness/bug/style findings. v1 "repo map" is lexical, not vector.

**Pitfalls.** Trusting the model to count lines; gating on self-reported confidence; posting N individual comments; exceeding ~100 comments/review; commenting on lines outside a hunk (422); sending whole files instead of hunks; building embeddings in v1; map-reduce as default; not stripping delete-only/generated files; re-reviewing the whole PR on every push; hardcoding a single model slug.

**Sources.** github.com/qodo-ai/pr-agent (`pr_processing.py`) · qodo-merge-docs.qodo.ai · coderabbit.ai/blog/the-art-and-science-of-context-engineering · lancedb.com/blog/case-study-coderabbit · docs.github.com/rest/pulls/reviews · boundaryml.com/blog/structured-outputs-create-false-confidence · datadoghq.com/blog/using-llms-to-filter-out-false-positives · arxiv.org/pdf/2509.01494

---

## 5. Custom rules / configuration / "learnings"

**Summary.** Dominant pattern is config-as-code: a repo-root control file (`.coderabbit.yaml` / `.pr_agent.toml`) plus a separate NL rules file (`path_instructions` / `best_practices.md`), precedence local > org > tool defaults. Custom rules are overwhelmingly **natural-language, injected into the prompt** (not compiled to matchers), optionally scoped by glob. Leading tools are **hybrid** (CodeRabbit bundles 40+ linters/SAST, feeds output to the LLM, then a verification pass de-noises). "Learnings" (vector-DB memory of dismissals) is a clear v2 feature.

**Key findings.**
- **Two-file split**: structured YAML/TOML (settings) + markdown NL rules (injected verbatim). CodeRabbit `reviews.path_instructions[] = {path: glob, instructions: NL}`; PR-Agent `best_practices.md` + `extra_instructions`.
- **NL, not deterministic**: structured rules-with-severity remains an open PR-Agent request (#1766). Add an advisory `severity` hint the model maps onto findings.
- **Path scoping**: glob (picomatch) for per-rule targeting + global include/exclude; PR-level skip via label / author allowlist; per-tool enable/disable.
- **Hybrid is materially cleaner**: Semgrep data — static-analysis-guided LLM detection yields true positives at ~37% lower cost; deterministic tools best for SQLi/XSS/secrets/style, LLM for IDOR/authz/business-logic. Greptile (pure-LLM) ~11 FP/run vs CodeRabbit's ~2. Plan a `tools:` config + a stage that runs linters and feeds SARIF to the model — even if v1 ships LLM-only.
- **Learnings = v2**: vector-DB-backed retrieval of dismissed suggestions + a dismissal-capture UX. For v1 get 80% via a user-editable `rules.md`.
- **Precedence + minimal config**: highest-precedence location wins; keep the file minimal; support `remote_config` for org-centralized rules; `profile` enum (chill/assertive) + capped `tone`.

**Pitfalls.** Don't build a rule DSL/AST engine in v1; pure-LLM rules drift to false positives without a de-noise step; token-budget blowup from dumping full rules.md + SARIF; free models vary in instruction-following; precedence ambiguity confuses users (log which won); over-broad ignore hides real changes; learnings carries privacy/storage burden.

**Sources.** docs.coderabbit.ai/getting-started/yaml-configuration · docs.coderabbit.ai/reference/configuration · docs.coderabbit.ai/knowledge-base · github.com/qodo-ai/pr-agent/blob/main/pr_agent/settings/configuration.toml · github.com/qodo-ai/pr-agent/issues/1766 · semgrep.dev/blog/2026/operationalizing-ai-powered-detection · greptile.com/greptile-vs-coderabbit · lancedb.com/blog/case-study-coderabbit

---

## 6. Security, abuse, cost & operability

**Summary.** For a code-review-only v1 the highest-leverage security decision is architectural: make the model a **pure read-and-comment function with zero write/merge/exec**, so prompt injection in PR content can at worst produce a bad comment — not exfiltrate secrets or auto-approve. The documented failures (Claude Code Security Review, Gemini CLI Action, Copilot Coding Agent all hijacked by a single PR title) stemmed from agents with tools + live secrets in the runtime ingesting untrusted input. BYOK keys: encrypted at rest, per-install isolated, never logged, never in the prompt; in Action mode use the repo's Actions secret so we never custody it. Cost/abuse control hinges on hard pre-LLM guards (max files/lines/tokens, dedup by diff hash, cancel-in-flight) because free models cap hard. Idempotency on `X-GitHub-Delivery` (App path) prevents double-reviews.

**Key findings.**
- **Injection is only dangerous with powers** → v1 agent has none; a separate trusted path posts schema-validated findings; approval/merge stays human.
- **Input fencing + data-not-instructions framing**; constrain output to a JSON schema; reject off-schema.
- **BYOK key**: Action mode → GitHub Actions encrypted secret (we never see it); key only as `Authorization: Bearer`, never logged/in-prompt. SaaS mode → KMS-backed per-install vault (v2).
- **Secrets in the diff** → gitleaks pre-scan + redact `[REDACTED_SECRET]` before sending; emit as a high-severity finding.
- **Free limits**: 20 RPM; 50/day (<$10) → 1000/day (≥$10); 402 on negative balance; failed calls count. Check `GET /api/v1/key` for `limit_remaining`.
- **Dedup/debounce/cancel-in-flight**: `concurrency` group keyed on repo+PR with `cancel-in-progress`; dedup by diff hash; short debounce to coalesce burst pushes.
- **Webhook idempotency** (App): persist `X-GitHub-Delivery`, no-op duplicates, verify HMAC first.
- **Privacy**: `provider.zdr=true` / training-opt-out default; never enable OpenRouter's own logging (grants irrevocable commercial-use rights); disclose; offer ZDR-only / BYOK-real-provider.
- **Actions trust**: `pull_request` (not `pull_request_target`); never execute PR code; least-privilege token; pin actions by SHA.
- **Observability**: per-review record (delivery_id, repo, pr#, sha, diff_hash, model, tokens, cached_tokens, est_cost, latency, redactions, outcome).

**Pitfalls.** Relying on the model to "resist" injection; `pull_request_target` + checkout; trusting LLM free-text (an "approve" verb); shipping with no ZDR/opt-out on proprietary code; per-file fan-out exhausting quota; not deduping redelivered webhooks; logging the full prompt/diff/key; assuming caching is free/universal; trusting OpenRouter's placeholder rate-limit docs page (verify via `/api/v1/key`).

**Sources.** rockcybermusings.com/p/ai-coding-agent-prompt-injection-procurement-failure · anthropic.com/research/prompt-injection-defenses · lakera.ai/blog/indirect-prompt-injection · openrouter.ai/docs/api/reference/limits · openrouter.zendesk.com/.../OpenRouter-Rate-Limits · openrouter.ai/docs/guides/features/zdr · securitylab.github.com/resources/github-actions-preventing-pwn-requests · docs.github.com/actions/reference/security/secure-use · github.com/gitleaks/gitleaks · wiz.io/blog/github-actions-security-guide

---

## Adversarial critique (what the first-draft design got wrong)

A skeptical-reviewer pass on the synthesized architecture surfaced:

- **Fork PRs get no secrets** — auto-review silently no-ops on external-contributor PRs (the OSS segment). → resolved by the **maintainer-gated `/review`** decision.
- **Concurrency across simultaneous PRs** shares one per-account quota — no control designed. → open question #2.
- **`models[]` "multiplies your ceiling" is likely false** (shared per-account counter, failed attempts count). → must verify via `GET /api/v1/key`.
- **Weekly per-model token allocations** unmodeled.
- **Incremental review after force-push** (`last_reviewed_sha` unreachable) needs an explicit full-review fallback.
- **Container action cold-start cost** the user pays in Actions minutes — unaddressed.
- **Over-abstraction**: four interfaces guarding single implementations → collapse to modules.
- **Scope creep**: self-reflection pass + tree-sitter/ctags + map-reduce → cut from v1.
