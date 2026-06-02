#!/usr/bin/env python3
"""Daily-cap exhaustion probe for OpenRouter free models.

DESTRUCTIVE: intentionally burns the key's free daily request quota to find the
account daily-limit transition. Free request quota resets ~midnight UTC.

Reads OPENROUTER_API_KEY from the environment (source ./.env first). Sends
forced-fallback array requests and classifies each outcome (SERVED / UPSTREAM_429
/ DAILY_LIMIT / 404 / capacity), stopping when the distinct daily-limit error
appears. See docs/spike-openrouter-quota.md for the 2026-06-02 result (cap did
NOT trigger after ~80 requests on a $0 free-tier key).

Usage:  set -a; . ./.env; set +a; python3 scripts/quota-exhaust.py
"""
import os, json, time, urllib.request, urllib.error

KEY = os.environ["OPENROUTER_API_KEY"]
URL = "https://openrouter.ai/api/v1/chat/completions"
# dead-id (404 skip) + usually-saturated coder + reliable backstop = realistic ~3x fan-out
MODELS = ["deepseek/deepseek-v4-flash:free", "qwen/qwen3-coder:free", "openai/gpt-oss-120b:free"]
MAX_REQUESTS = 60
SLEEP_S = 3.2  # stay under the 20 req/min free RPM cap


def call():
    body = json.dumps({"models": MODELS, "messages": [{"role": "user", "content": "ok"}], "max_tokens": 1}).encode()
    req = urllib.request.Request(URL, data=body, headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=60)
        return r.status, json.loads(r.read().decode(), strict=False)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode(), strict=False)
        except Exception:
            return e.code, {"error": {"code": e.code, "message": "<unparseable>"}}


def classify(status, d):
    if status == 200 and not d.get("error"):
        return "SERVED", str(d.get("model"))
    e = d.get("error") or {}
    msg = str((e.get("metadata") or {}).get("raw") or e.get("message") or "")
    code = e.get("code") or status
    low = msg.lower()
    if any(s in low for s in ["per day", "per-day", "daily", "add 10 credits", "add credits", "free-models-per-day", "requests per day"]):
        return "DAILY_LIMIT", msg
    if "upstream" in low:
        return "UPSTREAM_429", msg
    if code == 429:
        return "OTHER_429", msg
    if code == 404:
        return "404", msg
    return f"ERR_{code}", msg


def main():
    counts, served, t0 = {}, {}, time.time()
    for i in range(1, MAX_REQUESTS + 1):
        s, d = call()
        cat, info = classify(s, d)
        counts[cat] = counts.get(cat, 0) + 1
        if cat == "SERVED":
            k = info.split("/")[-1][:18]
            served[k] = served.get(k, 0) + 1
        print(f"{i:3d} [{s}] {cat} {info[:70]}", flush=True)
        if cat == "DAILY_LIMIT":
            print("\n>>> DAILY LIMIT HIT. exact error:")
            print(json.dumps(d.get("error"), indent=2)[:600])
            break
        time.sleep(SLEEP_S)
    print(f"\n=== SUMMARY ===\noutcome counts: {counts}\nserved-by: {served}\nelapsed: {int(time.time()-t0)}s")


if __name__ == "__main__":
    main()
