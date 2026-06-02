# Model Strategy (OpenRouter)

All inference goes through OpenRouter's OpenAI-compatible `/api/v1/chat/completions`, authenticated with the user's own key (BYOK).

> **Updated 2026-06-02 from the live spike** — see [`spike-openrouter-quota.md`](spike-openrouter-quota.md). The `models[]` array is hard-capped at **3**; `require_parameters: true` eliminates the free pool; `deepseek-v4-flash:free` is a dead ID; `gpt-oss-120b:free` is the only reliably-JSON free model.

## Default review chain

A config-defined, ordered `models[]` fallback array — **max 3 entries** (the API rejects more), never hardcoded (the `:free` roster churns), verified live:

```
openai/gpt-oss-120b:free   → only reliably-pure-JSON free model; lead the STRUCTURED call here
qwen/qwen3-coder:free      → strong pure-coder, 1M ctx (often upstream-saturated; defensive-parse)
z-ai/glm-4.5-air:free      → currently-serving fallback (thinking model — see caveats)
```

`deepseek/deepseek-v4-flash:free` was removed — it returns `404 No endpoints found` (dead ID). Lead with the JSON-reliable model because parseable findings are the binding requirement for posting comments; if pure code-reasoning matters more for a given call, lead with `qwen/qwen3-coder:free` and lean on the defensive parser (a per-call-type tradeoff). Other IDs seen mid-2026: `moonshotai/kimi-k2.6:free` (262K ctx, reasoning), `meta-llama/llama-3.3-70b-instruct:free`. **Treat all IDs as runtime config and liveness-probe them** — GLM-4.6 went paid, DeepSeek R1/V3 free aged out, `:free` slugs resolve to dated snapshots (`kimi-k2.6:free` → `kimi-k2.6-20260420:free`).

## Fallback mechanics

- **Always send `models[]`** (not a single model). OpenRouter auto-advances on 429 / context-overflow / moderation / downtime.
- `require_parameters: true` in provider routing so fallback only lands on providers that honor `response_format`.
- Read `response.model` back to attribute which model actually served (quality + telemetry).
- **429** → exponential backoff + jitter, then let the array advance. **Never blind-retry** — failed calls consume the daily quota.
- **402** (negative balance) → surface a clear "add OpenRouter credit" message.
- **404** ("no endpoints for this model") → auto-prune that id and continue.

> ⚠️ **Verify before relying on it:** the claim that a multi-model array "multiplies your daily ceiling" is probably **wrong** — the free cap is per-account and failed attempts debit the same counter, so a fallback array could burn up to N× quota on one struggling PR. Confirm against `GET /api/v1/key` behavior under a forced fallback before building the array as the default path.

## Token budget

Fetch `/api/v1/models` at startup, read each candidate's `context_length`, and set the compression cap **per-model dynamically** (default conservative: `context_length × 0.5`, reserving ~1500 tokens output headroom). Never hardcode 32k.

## Structured output

```jsonc
response_format: {
  type: "json_schema",
  json_schema: {
    name: "review_findings",
    strict: true,
    schema: {
      type: "object",
      additionalProperties: false,
      required: ["findings"],
      properties: {
        findings: {
          type: "array",
          items: {
            type: "object",
            additionalProperties: false,
            required: ["relevant_file","start_line","end_line","severity","category","comment"],
            properties: {
              relevant_file: { type: "string" },
              start_line:    { type: "integer" },
              end_line:      { type: "integer" },
              severity:      { type: "string", enum: ["critical","major","minor","nit"] },
              category:      { type: "string", enum: ["correctness","bug","security","style","design"] },
              comment:       { type: "string" },
              existing_code: { type: "string" },   // snippet copied from __new hunk__
              improved_code: { type: "string" },   // drop-in replacement → renders as a ```suggestion block
              confidence:    { type: "number" },   // sort/tiebreak only — NOT a hard gate
              rule_id:       { type: "string" }    // originating custom rule, if any
            }
          }
        }
      }
    }
  }
}
```

- **Do NOT pair with `require_parameters: true`** — the live spike showed it returns `404 No endpoints found` on the entire free pool. Send `json_schema` **best-effort** (compliant models honor it, others ignore it).
- The **defensive JSON-repair parser + single re-prompt is mandatory** (not occasional): the spike observed markdown-fenced JSON, **empty content** (thinking models that spent `max_tokens` on reasoning), and raw control characters that break strict parsing. Parse with control-char tolerance, strip ```` ``` ```` fences, re-prompt/advance on empty.
- **Thinking models** (glm-4.5-air, kimi): disable reasoning for the structured call or budget generous `max_tokens` — reasoning tokens are billed *outside* `max_tokens` and can leave visible content empty.
- `existing_code`/`improved_code` enable GitHub ` ```suggestion ` blocks matched by **content** (robust to line drift), not by line number.
- Two label modes (verbose vs critical-only) as a noise knob.
- Put the large static system + custom-rules block at the **prompt prefix** to maximize automatic prefix-cache hits (DeepSeek/Kimi support implicit caching, ~3–5 min TTL).

## Privacy default

Send `provider.zdr: true` (or set the account training-opt-out) by default; surface the free-model training tradeoff in onboarding. Offer **community mode** (`privacy.mode: community`) that widens the free pool with explicit disclosure. On empty ZDR route → fail loudly, do not silently route to a training provider. See [`decisions.md`](decisions.md#2-privacy-zdr-default--opt-in-community-mode).
