# Model Strategy (OpenRouter)

All inference goes through OpenRouter's OpenAI-compatible `/api/v1/chat/completions`, authenticated with the user's own key (BYOK).

## Default review chain

A config-defined, ordered `models[]` fallback array (never hardcoded — the `:free` roster churns):

```
qwen/qwen3-coder:free        → strongest free pure-coder, 1M context (whole PR in one call)
deepseek/deepseek-v4-flash:free → native reasoning for subtle bug/security, 1M context
openai/gpt-oss-120b:free     → OpenAI-lineage backstop, most reliable structured-output compliance
```

Lead with the 1M-context coder so a whole compressed PR fits in one call. (Trimmed from a 5-model array to ~3 per the critique: more models = more 404-pruning, more attribution surface, possibly more quota burn.)

Other free options seen mid-2026: `moonshotai/kimi-k2.6:free` (262K ctx, agentic), `z-ai/glm-4.5-air:free` (131K ctx, hybrid thinking), `meta-llama/llama-3.3-70b-instruct:free` (131K, generic backstop). Note: GLM-4.6 went paid; DeepSeek R1/V3 free aged out; Kimi is now K2.6 — **treat all IDs as runtime config.**

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

- Pair with `require_parameters: true` so fallback only lands on schema-honoring providers.
- Keep a **defensive JSON-repair parser + single re-prompt** — free providers occasionally emit JSON-as-text despite the directive.
- `existing_code`/`improved_code` enable GitHub ` ```suggestion ` blocks matched by **content** (robust to line drift), not by line number.
- Two label modes (verbose vs critical-only) as a noise knob.
- Put the large static system + custom-rules block at the **prompt prefix** to maximize automatic prefix-cache hits (DeepSeek/Kimi support implicit caching, ~3–5 min TTL).

## Privacy default

Send `provider.zdr: true` (or set the account training-opt-out) by default; surface the free-model training tradeoff in onboarding. Offer **community mode** (`privacy.mode: community`) that widens the free pool with explicit disclosure. On empty ZDR route → fail loudly, do not silently route to a training provider. See [`decisions.md`](decisions.md#2-privacy-zdr-default--opt-in-community-mode).
