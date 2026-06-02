# Spike: OpenRouter free-model behavior

Run 2026-06-02 against a real free ($0-credit, `is_free_tier: true`) OpenRouter key. Reproduce with `bash scripts/quota-spike.sh`. The free pool is volatile — re-run before trusting any specific model ID or availability claim.

## TL;DR — what changed in the design

1. **`models[]` is hard-capped at 3.** Arrays > 3 return `400 "'models' array must have 3 items or fewer."` Not a guideline — the API rejects it.
2. **Drop `require_parameters: true`.** Combined with `json_schema` it returns `404 "No endpoints found that can handle the requested parameters"` on **every** free model — it eliminates the entire free pool. (The original `model-strategy.md` recommended this exact combo; corrected.)
3. **`gpt-oss-120b:free` is the only reliably-JSON free model.** Use it for the findings-generation (structured) call.
4. **A defensive JSON parser is mandatory, not optional** — fenced output, empty content, and raw control characters were all observed in practice.
5. **The "50 requests/day" free cap did NOT reproduce.** A forced-fallback exhaustion run (~80 requests today, ~180 sub-attempts) on a $0 `is_free_tier` key never hit an account daily limit. The real binding constraint is **upstream provider saturation**, which the fallback array directly mitigates. See "Exhaustion run" below.

## Findings

### Quota observability
- `GET /api/v1/key` exposes only **credit (USD) usage** (`usage`, `usage_daily/weekly/monthly`) — all `$0` for free models — plus `is_free_tier`. It does **not** expose the free daily request count. The `rate_limit` field is deprecated (`requests: -1`).
- Chat-completion responses return **no `X-RateLimit-*` headers**. On a 429 you get only `retry-after`.
- **Consequence:** the 50/1000-per-day free counter is invisible to introspection. Determining whether a `models[]` fallback debits it once or per-attempt requires actually exhausting it (~50 requests on a $0 key) and watching for the distinct daily-limit error.

### Model availability (snapshot — volatile)
| Model | Result at run time |
|---|---|
| `qwen/qwen3-coder:free` | 429 **upstream** rate-limited (provider Venice) |
| `deepseek/deepseek-v4-flash:free` | **404 No endpoints found** — dead/unroutable ID |
| `moonshotai/kimi-k2.6:free` | 429 upstream (single) / **served via provider Crucible** in fallback |
| `z-ai/glm-4.5-air:free` | served |
| `openai/gpt-oss-120b:free` | served |

- **`:free` slugs resolve to dated snapshots** — `moonshotai/kimi-k2.6:free` served as `moonshotai/kimi-k2.6-20260420:free`.
- **Upstream 429s are common and independent of your own quota** ("temporarily rate-limited upstream") — free models share a global provider pool. This is the main reason a fallback array is *necessary*, not just nice-to-have.

### Fallback array behavior
- **Routes around upstream-429:** `[qwen(429), kimi, glm-air]` advanced past the saturated qwen and served kimi.
- **Auto-skips dead/404 models:** `[deepseek-v4-flash(404), glm-air]` served glm-air. A dead ID in the array doesn't break the request as long as a working model follows (still prune dead IDs so you don't waste one of your 3 slots).

### Structured output (the big one)
| Strategy | `glm-4.5-air:free` | `gpt-oss-120b:free` |
|---|---|---|
| `json_schema` + `require_parameters:true` | ❌ 404 no endpoints | ❌ 404 no endpoints |
| `json_schema` (best-effort, no require) | ⚠️ markdown-fenced | ✅ valid pure JSON |
| `json_object` mode | ❌ empty content | ⚠️ markdown-fenced |
| plain prompt → "JSON only" | ❌ empty content | ✅ valid pure JSON |

- **`glm-4.5-air` is a thinking model**: with `max_tokens: 200` it returned **empty visible content** (reasoning consumed the budget) or markdown-fenced JSON. Either disable thinking mode or give thinking models a much larger output budget.
- **Reasoning tokens are billed outside `max_tokens`**: a `max_tokens: 4` call to kimi returned 36 completion tokens (35 "reasoning"). Reasoning models can blow token/latency budgets you thought you'd capped.

## Revised structured-output strategy (replaces the old recommendation)

1. **Never** send `provider.require_parameters: true` with `response_format` on the free pool.
2. Generate findings on a JSON-reliable model — **lead the structured call with `openai/gpt-oss-120b:free`**.
3. Send `response_format: {type: "json_schema", ...}` **best-effort** (helps compliant models, ignored by others) — do not rely on it being enforced.
4. **Defensive parser is required:** strip ```` ```json ```` fences, parse with control-char tolerance (`strict=False`), and on empty/invalid content re-prompt once (and/or advance the model). 
5. For thinking models (glm-4.5-air, kimi), disable reasoning for the structured call or budget generous `max_tokens`.

## Revised default model chain (≤3, live-verified)

Lead with the JSON-reliable reasoner; fall back to currently-serving models. `deepseek-v4-flash:free` removed (dead). Verify at runtime via `/api/v1/models` + a liveness probe.

```
["openai/gpt-oss-120b:free", "qwen/qwen3-coder:free", "z-ai/glm-4.5-air:free"]
```

(If pure code-reasoning quality matters more than JSON reliability for a given call, lead with `qwen/qwen3-coder:free` and lean harder on the defensive parser — a real tradeoff to decide per call type.)

## Exhaustion run (settles share-vs-multiply, in practice)

Ran `scripts/quota-exhaust.py` — 60 forced-fallback requests `[deepseek-v4-flash(dead 404), qwen3-coder, gpt-oss-120b]`, each fanning out to ~3 sub-attempts (≈180 provider attempts), on top of ~20 from earlier probes ≈ **~80 requests today** on a $0 `is_free_tier: true` key.

**Result: `hit_daily: False`.** No account daily-limit error ever appeared.

```
outcome counts: { SERVED: 58, ERR_503: 1, UPSTREAM_429: 1 }   (of 60)
served-by:      { gpt-oss-120b:free: 58 }                      (qwen served 0/60)
elapsed:        651s  (~10.85s/req; ~7.6s latency, fallthrough waits on qwen's 429)
```

**What this means:**
- **The "50 requests/day" figure from secondary research did not reproduce.** With realistic 3× fan-out and ~80 requests, no daily cap. Either that number is outdated/wrong for mid-2026, or it isn't enforced as a simple per-request daily counter for these models. *The dreaded "fallback multiplies your 50/day and burns it instantly" risk did not materialize.*
- **The fallback array is clearly net-positive, demonstrated live:** the lead coder (`qwen3-coder:free`) was upstream-saturated for the *entire* run (0/60), yet the array delivered a **97% success rate** by falling through to `gpt-oss-120b:free`. Without the array, this would have been a near-total outage.
- **The operative constraint is upstream provider availability**, not the account quota: the only 2 failures were a `503 "No backends available"` (capacity) and one all-models-down `429`.
- **Latency caveat:** forced fallthrough adds ~7s/request because it waits on the saturated lead model before advancing. In production, lead with a model that's actually serving (or probe liveness) to avoid paying this on every call.

**Caveats / not fully closed:** ~80 requests is a modest sample; limits may be enforced over longer rolling windows, and the per-model **weekly token allocation** ceiling was not tested. Confirm the *current* official free-tier limits before launch, and keep the request-minimization discipline regardless. But the headline fear (immediate 50/day exhaustion from fan-out) is **not supported by the data**.

## Recommendation

Build the `models[]` fallback array (≤3) as the default — it's the single biggest reliability lever given how often free models are upstream-saturated. Do **not** over-engineer around a 50/day cap that didn't appear; do keep "one call per PR" discipline and liveness-aware lead-model selection (to dodge the fallthrough latency tax).
