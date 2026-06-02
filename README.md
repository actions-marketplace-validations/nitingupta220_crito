# review-agent

An AI pull-request review agent that runs on the **user's own [OpenRouter](https://openrouter.ai) key (BYOK)**, defaulting to strong **free** coding/reasoning models. It reviews a GitHub PR for correctness, bugs, security, style, and design — driven by the model's general coding knowledge plus optional natural-language custom rules.

> **Status:** design phase. No implementation yet. This repo currently holds the research and architecture findings that the build will follow.

---

## The thesis

Every incumbent — CodeRabbit ($12–24/seat), Qodo (~$19), Graphite ($20–40), Bito ($15–25) — charges per-seat for what is fundamentally a **diff-compression → structured-LLM-call → post-comment** pipeline. We eliminate the inference cost by running on the user's own OpenRouter key against free coding models (Qwen3-Coder, DeepSeek V4 Flash, Kimi K2.6, GLM-4.5-Air). Shipping as a **self-hosted GitHub Action** means the user's code and key never touch our servers — that's both the trust story and a zero-infra story for us.

We are **not** building the expensive moat (codebase-graph/embeddings RAG, multi-agent swarms, 40+ bundled linters). We win on price and on a tight, low-false-positive review of the diff, customizable via natural-language rules.

### Two honest caveats

- **It's "~$10 one-time", not "$0".** Free OpenRouter models cap at **20 req/min and 50 req/day** until a one-time **$10 credit purchase** permanently raises the daily cap to **1000/day**. The 50/day tier is a demo tier, not a working tier for an active repo.
- **"Free models" and "private code" are in tension.** Most `:free` models only route if the account allows training on prompt data. Forcing zero-data-retention (ZDR) may shrink the free pool to almost nothing. So per repo you choose: privacy or maximum-free.

---

## v1 scope (code review only)

- Published **GitHub Action** installed via a ~15-line workflow; `OPENROUTER_API_KEY` lives in the user's repo secrets; we custody nothing.
- Triggers: `pull_request` (auto-review on same-repo PRs) + a maintainer `/review` comment (the only secret-safe path for forked PRs).
- Diff ingest → ignore-glob filter → **secret pre-scan + redaction** → reference-line-number injection → compression-to-one-call → OpenRouter (`models[]` fallback + `json_schema`) → parse/repair → anchor-validate + dedup + gate → **one batched GitHub Review + one persistent summary comment**, with incremental review on new pushes.
- Two-file config: `.pr-review.yaml` (settings) + `.pr-review/rules.md` (natural-language rules), glob-matched into the prompt.
- Security posture: agent is **pure read-and-comment with zero write/merge/exec** — neutralizes prompt injection from PR content.

## Explicitly deferred to later

Managed GitHub App + webhook SaaS (the "managed flip") · codebase-graph/embeddings RAG · learnings/memory loop · hybrid deterministic linters (semgrep/eslint/ruff) · `/describe` and `/ask` commands · GitLab/Bitbucket providers · auto-fix-commit · self-reflection re-scoring pass.

---

## Direction decisions (soft — subject to revision)

| Decision | Choice | Why |
|---|---|---|
| Target repos | **OSS too, maintainer-gated** | Same-repo PRs auto-review; forked PRs reviewed only when a trusted maintainer comments `/review` (forks get no secrets; `pull_request_target` is banned as the pwn-request RCE vector). |
| Privacy | **ZDR default + opt-in community mode** | Safe-by-default for proprietary code; user explicitly widens the free pool. ZDR-strict with zero providers → fail loudly, never silently route to a training provider. |
| Distribution | **GitHub Action first** | Zero infra, zero key custody. The GitHub App is the v2 "managed flip". |

---

## Recommended build sequence

1. **Diff → findings core** (no GitHub): ingest fixture diff → filter → reference-line-number injection → compress → OpenRouter call → parse/repair → gated findings. **Spike first:** verify the `GET /api/v1/key` quota-sharing question before committing to the fallback array as default.
2. **GitHub adapter**: `GET /pulls/{n}/files` → hunk-anchor index → batched `POST /pulls/{n}/reviews` + persistent summary comment + `last_reviewed_sha` marker.
3. **Action packaging + same-repo auto-review** end-to-end on a private test repo.
4. **OSS gate**: the `issue_comment` `/review` workflow + authorization check + "comment to review" hint on fork PRs.
5. **Config + custom rules**: `.pr-review.yaml` + `.pr-review/rules.md`, chill default profile.
6. **Secret pre-scan + privacy defaults + telemetry**.

---

## Documents

- [`docs/architecture.md`](docs/architecture.md) — internal architecture, components, end-to-end flow, trigger matrix.
- [`docs/decisions.md`](docs/decisions.md) — the three decisions, rationale, open questions, top risks.
- [`docs/model-strategy.md`](docs/model-strategy.md) — OpenRouter model chain, fallback, structured output, privacy mechanics.
- [`docs/custom-rules.md`](docs/custom-rules.md) — config schema and how rules reach the model.
- [`docs/security-and-ops.md`](docs/security-and-ops.md) — prompt injection, secret handling, rate limits, idempotency, observability.
- [`docs/research-findings.md`](docs/research-findings.md) — full research across six dimensions, with sources.
- [`docs/spike-openrouter-quota.md`](docs/spike-openrouter-quota.md) — live OpenRouter spike: 3-model cap, `require_parameters` landmine, structured-output reality, the daily-cap exhaustion result.
- [`docs/spike-prompt-quality.md`](docs/spike-prompt-quality.md) — does the review actually work? Planted-issue eval: 0 false positives, 100% injection resistance, recall is the weak spot.

## Scripts

- [`scripts/quota-spike.sh`](scripts/quota-spike.sh) — reproducible OpenRouter free-model probes (availability, fallback, structured output).
- [`scripts/quota-exhaust.py`](scripts/quota-exhaust.py) — **destructive** daily-cap exhaustion probe (burns the free daily quota).
- [`scripts/prompt-quality-spike.py`](scripts/prompt-quality-spike.py) — seed eval harness: scores models on a planted-issue diff (recall / precision / injection).
