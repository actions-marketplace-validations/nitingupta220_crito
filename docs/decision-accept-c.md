# Decision Record: Accept Option C

**Date:** 2026-06-13
**Status:** Accepted
**Supersedes:** the merged "full multi-agent system" on `origin/main`
**Owner:** tarun.chaudhary@infrax.ai

---

## 1. Decision

Ship **Option C** as v1: the documented **lean, zero-infra, pure read → comment BYOK GitHub Action**.

- **Ship:** a composite GitHub Action that reads a PR diff and posts review comments. No server, no database, no dashboard. The user brings their own OpenRouter key (BYOK); the key and the code never leave the runner.
- **Salvage:** the spike-validated review **engine** from merged PR #2 (`origin/main`) — specifically `app/services/openrouter_service.py` and the caps/skip logic in `scripts/action_review.py`.
- **Park:** the FastAPI server + Postgres + Next.js dashboard. Removed from the v1 runtime. Reintroduced **later, opt-in**, as a v2 **managed** offering — not deleted, but not on the v1 critical path.

Net: take the validated brain, drop the heavy body, honor the design docs.

---

## 2. Context

Three git states are in play, and they disagree:

| State | What it is |
|---|---|
| local `main` | **Design docs only** — `docs/` (architecture, decisions, model-strategy, research-findings, security-and-ops, two spike write-ups). Describes a lean Action. |
| `origin/main` | The merged **"full multi-agent system"**: FastAPI (`app/main.py`), a **6-agent swarm** (`app/agents/` — bug, security, performance, quality, docs, aggregator), Postgres (`app/database.py`, `app/models/db_models.py`), and a **Next.js dashboard** (`frontend/`). |
| `origin/feature/add-ai-pr-review-agent` | A **lighter Action**-shaped variant. |

The conflict: **the merged implementation contradicts the design docs.** The docs scope a lean Action; `origin/main` shipped a managed multi-agent platform. This record resolves the contradiction in favor of the docs, while salvaging the one part of `origin/main` that is genuinely valuable (the engine).

---

## 3. Why not B (keep the heavy server)

Rejected. Keeping the FastAPI + swarm + DB + dashboard stack violates the v1 charter on three independent axes:

1. **The docs already cut it.** "Multi-agent swarm" is named a v1 **non-goal twice** (`docs/decisions.md`), and the managed SaaS is explicitly named the **deferred v2 "managed flip."** Keeping B re-litigates a settled non-goal.
2. **It is the worst posture against the binding constraint.** The spikes establish that the binding constraint is **upstream saturation** (OpenRouter free-tier daily-cap / rate limits — see `docs/spike-openrouter-quota.md`). The 6-agent swarm makes **6 LLM calls per PR over a ~30k-token diff**, i.e. ~6× the saturation pressure of a single ensemble call. B maximizes exactly the quantity the spike says to minimize.
3. **It forfeits the security thesis.** The product promise is **"your code & key never touch our servers."** A server that custodies the key and ingests the diff server-side throws that away on day one. B cannot make the central marketing/security claim true.

---

## 4. Why not A (pure rebuild)

Rejected. A from-scratch rebuild **re-pays for findings we already own.**

`origin/main`'s `app/services/openrouter_service.py` already independently re-derived **every** spike finding:

- the **3-model cap** (free tier),
- **no `require_parameters`** (it silently drops free models),
- a **tolerant JSON parser** (free models wrap/garble structured output),
- a **thinking-model token floor** (reasoning models need more headroom or return empty).

That file is the dividend of the quota/quality spikes already paid for in code. Rebuilding would re-derive it at cost and risk regressing the hard-won fixes. **Salvage the engine; rebuild only the harness around it.**

---

## 5. Salvage table

| Keep (port into `prreview/`) | Discard from v1 (park for v2) |
|---|---|
| `app/services/openrouter_service.py` — the engine (3-model cap, no `require_parameters`, tolerant parser, thinking-model token floor) | `app/main.py` (FastAPI app) |
| `scripts/action_review.py` — `SKIP_PATTERNS`, finding **sort**, per-PR **caps** | `app/routers/*` — unauth `/trigger` + `/reviews` (and `webhook.py`) |
| The **findings JSON schema** | `app/database.py` + `app/models/db_models.py` + Postgres |
| `ANTI_FALSE_POSITIVE_DIRECTIVE` | `frontend/` (Next.js dashboard) |
| The 5 specialists' **checklists**, folded into **ONE ensemble prompt** | The 5 `app/agents/*` as **separate LLM calls** |
| | `app/services/static_analysis_service.py` (Bandit/Pylint run on **+lines only**) |
| | `Dockerfile` (`--reload`) + `docker-compose.yml` |

---

## 6. Recall strategy

Recall comes from **model diversity**, not from more calls.

- **Single-prompt ensemble:** one prompt, run across **≤3 DIFFERENT models**, then **union + dedup** the findings. This was the spike's **single biggest quality lever**: `union(gpt-oss + glm) ≥ 4/6` planted issues caught at **0 false positives** (`docs/spike-prompt-quality.md`).
- **Fold, don't fan out:** the 5 specialist checklists live **inside the one ensemble prompt**, not as 5 separate agents/calls.
- **Honest recall ceiling.** An LLM-only ensemble will miss things. We say so. The answer to the ceiling is **deferred hybrid linters** (semgrep / ruff) on the near roadmap — deterministic recall, near-zero marginal cost.
- **HARD CAP: ≤3 union members.** If the ensemble misses, the remedy is **linters, never more LLM calls.** Adding a 4th model re-creates the Option-B saturation problem we just rejected.

---

## 7. Must-fix security (baked into this build)

These are not roadmap items — they ship **in v1**:

- **(a) Untrusted-input fencing.** The diff is wrapped in explicit `<UNTRUSTED_DIFF>...</UNTRUSTED_DIFF>` delimiters; the system prompt instructs the model to treat everything inside as data, never as instructions (prompt-injection defense).
- **(b) Never post LLM output verbatim.** Sanitize model output before it becomes a comment — neutralize `@`-mentions (no mass-pings) and strip/escape HTML before posting.
- **(c) `/review` authz.** Gate the `/review` command on `author_association` (e.g. OWNER / MEMBER / COLLABORATOR) so arbitrary commenters cannot trigger spend.
- **(d) Secret redaction before the model.** gitleaks-style **regex redaction** of the diff before it is sent upstream, so secrets in a PR are not exfiltrated to the model provider.
- **(e) Safe trigger.** Use `pull_request`, **not** `pull_request_target`, and **never execute fork code.** No checkout-and-run of untrusted PR contents.

---

## 8. New module layout

```
prreview/                 # the v1 Python package (engine + harness)
.github/workflows/        # the workflow that invokes the Action
action.yml                # composite action definition
```

The salvaged engine and caps/skip logic move into `prreview/`; the server, DB, dashboard, and per-agent fan-out do not.

**Note:** the v1 secret scan (security item **d**) is **regex-based**. The **gitleaks binary is an optional upgrade step**, not a v1 dependency — keeping the Action zero-infra and dependency-light by default.
