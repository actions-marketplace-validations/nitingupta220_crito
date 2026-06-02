# Spike: OpenRouter free-model behavior

Run 2026-06-02 against a real free ($0-credit, `is_free_tier: true`) OpenRouter key. Reproduce with `bash scripts/quota-spike.sh`. The free pool is volatile — re-run before trusting any specific model ID or availability claim.

## TL;DR — what changed in the design

1. **`models[]` is hard-capped at 3.** Arrays > 3 return `400 "'models' array must have 3 items or fewer."` Not a guideline — the API rejects it.
2. **Drop `require_parameters: true`.** Combined with `json_schema` it returns `404 "No endpoints found that can handle the requested parameters"` on **every** free model — it eliminates the entire free pool. (The original `model-strategy.md` recommended this exact combo; corrected.)
3. **`gpt-oss-120b:free` is the only reliably-JSON free model.** Use it for the findings-generation (structured) call.
4. **A defensive JSON parser is mandatory, not optional** — fenced output, empty content, and raw control characters were all observed in practice.
5. **The free daily request counter (50/1000/day) is not observable** via any endpoint or header. "Share vs multiply" can only be settled by exhausting the cap.

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

## Still open: share vs multiply

Not yet settled (counter is unobservable). What we *do* know bounds the risk:
- The array is capped at **3**, so worst case is **3×**, not the 5× the critique feared.
- Dead-model (404) skips are cheap and may not count as provider attempts.
- The mitigation is the same regardless of the answer: **minimize calls per PR, lead with the most-reliable model, and treat every request as potentially counting.**

A definitive answer needs a one-time **daily-cap exhaustion run** on this $0 key (burns the 50/day until midnight UTC). Pending owner go-ahead — see the run note in the chat.
