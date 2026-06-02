#!/usr/bin/env bash
# OpenRouter free-model spike — reproducible probes for the PR review agent.
# Reads OPENROUTER_API_KEY from ./.env (gitignored). Never prints the key.
# Usage:  bash scripts/quota-spike.sh
#
# Answers the load-bearing questions before we build on free models:
#   - which free model IDs actually route right now (the roster churns)
#   - does the models[] fallback array route around upstream-429s
#   - what is the hard cap on the fallback array size
#   - which structured-output strategy actually yields parseable JSON
# NOTE: the free *daily request counter* (50/1000/day) is NOT exposed by any
# OpenRouter endpoint or header, so "does fallback share or multiply the daily
# quota" can only be settled by exhausting the cap (see docs/spike-openrouter-quota.md).
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "missing .env with OPENROUTER_API_KEY"; exit 1; }
set -a; . ./.env; set +a
BASE=https://openrouter.ai/api/v1
hdr=(-H "Authorization: Bearer $OPENROUTER_API_KEY" -H "Content-Type: application/json")

# Candidate free models (treat as DATA — verify live, IDs churn).
MODELS=(
  "qwen/qwen3-coder:free"
  "deepseek/deepseek-v4-flash:free"
  "moonshotai/kimi-k2.6:free"
  "z-ai/glm-4.5-air:free"
  "openai/gpt-oss-120b:free"
)

j() { python3 -c "import json,sys;print(json.loads(sys.stdin.read(),strict=False)$1)" 2>/dev/null || echo "<parse-error>"; }

echo "=== /api/v1/key (credit usage + is_free_tier; NO free-request counter here) ==="
curl -sS "${hdr[@]%/-H Content-Type*}" -H "Authorization: Bearer $OPENROUTER_API_KEY" "$BASE/key" \
  | j "['data']" | python3 -c "import ast,sys;d=ast.literal_eval(sys.stdin.read());print({k:d.get(k) for k in ['is_free_tier','usage','usage_daily','usage_weekly','limit_remaining']})" 2>/dev/null || true

echo; echo "=== per-model availability (single-model requests) ==="
for m in "${MODELS[@]}"; do
  b=$(curl -sS "${hdr[@]}" "$BASE/chat/completions" \
    -d "{\"models\":[\"$m\"],\"messages\":[{\"role\":\"user\",\"content\":\"one word: ok\"}],\"max_tokens\":4}")
  printf '  %-36s -> %s\n' "$m" "$(echo "$b" | python3 -c "import json,sys;d=json.loads(sys.stdin.read(),strict=False);e=d.get('error');print(('SERVED '+str(d.get('model'))) if not e else ('ERR '+str(e.get('code'))+' '+str((e.get('metadata') or {}).get('raw',e.get('message')))[:70]))")"
  sleep 1
done

echo; echo "=== fallback array size cap (expect 400 if >3) ==="
curl -sS "${hdr[@]}" "$BASE/chat/completions" \
  -d '{"models":["a","b","c","d"],"messages":[{"role":"user","content":"x"}],"max_tokens":1}' \
  | j "['error']['message']"

echo; echo "=== structured-output matrix (served + is content pure JSON?) ==="
SCHEMA='"json_schema":{"name":"finding","strict":true,"schema":{"type":"object","additionalProperties":false,"required":["file","line","severity","comment"],"properties":{"file":{"type":"string"},"line":{"type":"integer"},"severity":{"type":"string","enum":["critical","major","minor","nit"]},"comment":{"type":"string"}}}}'
MSG='A function dereferences a possibly-null pointer at line 10 of foo.py. Emit exactly one finding as JSON with keys file,line,severity,comment.'
probe() { # $1 model  $2 label  $3 extra-json
  curl -sS "${hdr[@]}" "$BASE/chat/completions" \
    -d "{\"models\":[\"$1\"],\"messages\":[{\"role\":\"user\",\"content\":\"$MSG\"}],\"max_tokens\":200$3}" \
  | python3 -c "
import json,sys
d=json.loads(sys.stdin.read(),strict=False); e=d.get('error')
if e: print(f'  {\"$2\":40s} -> ERR {e.get(\"code\")}'); raise SystemExit
c=(d.get('choices') or [{}])[0].get('message',{}).get('content') or ''
try: json.loads(c,strict=False); v='VALID pure-JSON'
except Exception: v=('EMPTY' if not c else 'NOT pure-JSON: '+repr(c[:40]))
print(f'  {\"$2\":40s} -> {v}')"
  sleep 1
}
for m in "openai/gpt-oss-120b:free" "z-ai/glm-4.5-air:free"; do
  echo "### $m"
  probe "$m" "json_schema + require_parameters" ',"response_format":{"type":"json_schema",'"$SCHEMA"'},"provider":{"require_parameters":true}'
  probe "$m" "json_schema (best-effort)"        ',"response_format":{"type":"json_schema",'"$SCHEMA"'}'
  probe "$m" "json_object mode"                 ',"response_format":{"type":"json_object"}'
  probe "$m" "plain prompt"                     ''
done
