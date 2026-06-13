"""
OpenRouter LLM gateway — standalone client for the PR review agent.

Sends a chat-completion request to OpenRouter's unified API using the models[]
fallback array, and parses the response as JSON with a defensive multi-stage
parser. Pure standalone module: depends only on httpx + the Python stdlib.

Spike-validated design (lifted from the original service, re-expressed standalone):
- models[] fallback array, capped at 3 — the single biggest reliability lever.
  OpenRouter auto-routes around 429s, 404s, and context-length errors across
  the array, so we let the array advance instead of blind-retrying one model.
- require_parameters is NEVER sent — it filters out the entire free-model pool.
- response_format is sent best-effort only (a hint); never enforced/strict.
- Hardened JSON parser: strip markdown fences, strict/non-strict json.loads,
  brace-extraction, control-char tolerance; returns None on unrecoverable input.
- Thinking-model guard: GLM / Kimi / o1 / o3 / deepseek-r1 burn output budget on
  internal reasoning and return EMPTY visible content unless given a larger
  max_tokens floor.
- 429 / 503 -> exponential backoff + jitter, bounded by a small retry cap.
- The actual served model is read from the response `model` field and returned.
"""
import json
import random
import re
import time
from typing import Optional

import httpx

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

MAX_MODELS = 3

# Models with internal reasoning/thinking that consume extra output tokens.
# They must be given a larger output budget or visible content comes back empty.
_THINKING_MODEL_PATTERNS = ("glm", "kimi", "o1", "o3", "deepseek-r1")
_THINKING_MIN_TOKENS = 8192

# Floor for ANY request's output budget. Below this, even non-thinking free
# models truncate the structured-JSON findings array mid-stream on a large diff
# (finish_reason="length" -> unparseable -> the model silently contributes zero
# findings). 8192 leaves headroom for a full ~30-finding review with suggestions.
_MAX_TOKENS_FLOOR = 8192

# Retry policy for transient throttling (429) / unavailability (503).
# Small cap on purpose: the models[] array is the primary fallback mechanism,
# retries here only cover the whole-array being briefly throttled.
_MAX_RETRIES = 3
_BACKOFF_BASE = 2.0      # seconds
_BACKOFF_CAP = 16.0      # seconds
_RETRYABLE_STATUS = (429, 503)


# ---------------------------------------------------------------------------
# Token-budget helpers
# ---------------------------------------------------------------------------

def _is_thinking_model(model_id: str) -> bool:
    """True if the model does internal reasoning and needs extra output budget."""
    if not model_id:
        return False
    return any(p in model_id.lower() for p in _THINKING_MODEL_PATTERNS)


def _safe_max_tokens(model_id: str, requested: Optional[int]) -> int:
    """
    Clamp the output budget so we never under-fund a request.

    - Always enforce a 4096 floor (structured JSON gets truncated below this).
    - Bump thinking models (glm/kimi/o1/o3/deepseek-r1) to a higher floor so they
      don't spend the whole budget thinking and return empty visible content.
    """
    base = _MAX_TOKENS_FLOOR if not requested or requested < _MAX_TOKENS_FLOOR else int(requested)
    if _is_thinking_model(model_id) and base < _THINKING_MIN_TOKENS:
        return _THINKING_MIN_TOKENS
    return base


# ---------------------------------------------------------------------------
# Defensive JSON parsing
# ---------------------------------------------------------------------------

def _strip_json_fences(text: str) -> str:
    """Strip ```json ... ``` / ``` ... ``` markdown fences from model output."""
    text = text.strip()
    # Opening fence with optional language tag.
    text = re.sub(r"^```(?:json|JSON)?\s*\n?", "", text)
    # Closing fence.
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


def _parse_json_tolerant(raw: str) -> Optional[object]:
    """
    Parse JSON with increasing tolerance. Returns the parsed object, or None if
    every recovery attempt fails.

    Stages:
      1. strict json.loads
      2. strip markdown fences, retry
      3. non-strict (tolerate raw control chars inside strings)
      4. brace-extract the first {...} (or [...]) span and retry non-strict
    """
    if not raw or not raw.strip():
        return None

    # Stage 1: strict.
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Stage 2: de-fence and retry.
    cleaned = _strip_json_fences(raw)
    if cleaned and cleaned != raw:
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass
    else:
        cleaned = raw.strip()

    # Stage 3: non-strict tolerates unescaped control chars in string values.
    try:
        return json.loads(cleaned, strict=False)
    except json.JSONDecodeError:
        pass

    # Stage 4: brace/bracket extraction — find the outermost JSON container and
    # try to parse just that span (handles prose wrapped around the JSON).
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = cleaned.find(open_ch)
        end = cleaned.rfind(close_ch)
        if start != -1 and end != -1 and end > start:
            snippet = cleaned[start : end + 1]
            try:
                return json.loads(snippet, strict=False)
            except json.JSONDecodeError:
                continue

    return None


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class OpenRouterClient:
    """
    Minimal async OpenRouter client.

    Usage:
        client = OpenRouterClient(api_key, models=[...])
        parsed, served_model = await client.chat_json(system, user,
                                                       response_format=FINDINGS_SCHEMA)
    """

    BASE_URL = "https://openrouter.ai/api/v1"

    def __init__(self, api_key: str, models: list, timeout: float = 120.0):
        self.api_key = api_key
        # Cap the fallback array to <=3. OpenRouter auto-routes across it.
        self.models = list(models)[:MAX_MODELS]
        self.timeout = timeout
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # Identifying headers (optional, recommended by OpenRouter).
            "HTTP-Referer": "https://github.com/review-agent",
            "X-Title": "review-agent",
        }

    # -- internal HTTP -------------------------------------------------------

    async def _post_chat(self, payload: dict) -> dict:
        """
        POST one chat-completion request with bounded backoff on 429/503.

        Backoff is exponential with jitter. After the retry cap is hit on a
        retryable status, the error is raised so the caller can advance/handle;
        OpenRouter's own models[] routing is the primary fallback.
        """
        last_exc: Optional[Exception] = None
        async with httpx.AsyncClient(timeout=self.timeout) as http:
            for attempt in range(_MAX_RETRIES):
                try:
                    resp = await http.post(
                        f"{self.BASE_URL}/chat/completions",
                        headers=self.headers,
                        json=payload,
                    )
                except httpx.RequestError as exc:
                    # Network-level error (DNS, connect, read timeout). Treat as
                    # transient and back off, up to the cap.
                    last_exc = exc
                    if attempt >= _MAX_RETRIES - 1:
                        raise
                    self._sleep_backoff(attempt)
                    continue

                if resp.status_code in _RETRYABLE_STATUS and attempt < _MAX_RETRIES - 1:
                    self._sleep_backoff(attempt, resp)
                    continue

                resp.raise_for_status()
                return resp.json()

        # Unreachable in practice, but keep the type checker / fallback honest.
        if last_exc is not None:
            raise last_exc
        raise httpx.HTTPError("chat completion failed after retries")

    @staticmethod
    def _sleep_backoff(attempt: int, resp: "Optional[httpx.Response]" = None) -> None:
        """Exponential backoff with jitter; honor Retry-After when present."""
        delay = min(_BACKOFF_CAP, _BACKOFF_BASE * (2 ** attempt))
        if resp is not None:
            retry_after = resp.headers.get("retry-after")
            if retry_after:
                try:
                    delay = min(_BACKOFF_CAP, float(retry_after))
                except (TypeError, ValueError):
                    pass
        delay += random.uniform(0, delay * 0.25)  # jitter
        time.sleep(delay)

    # -- public API ----------------------------------------------------------

    async def chat_json(
        self,
        system: str,
        user: str,
        max_tokens=None,
        response_format=None,
    ) -> tuple:
        """
        Send (system, user) to the models[] array and return parsed JSON.

        Returns:
            (parsed_obj_or_None, served_model_id)

        - Sends the full models[] fallback array (already capped at <=3).
        - response_format is forwarded best-effort (a hint). require_parameters
          is NEVER sent.
        - Output budget is clamped via _safe_max_tokens (4096 floor, thinking-model
          bump) so glm/kimi don't return empty content.
        - Returns (None, served_model) on empty content or unrecoverable JSON.
        """
        if not self.models:
            raise ValueError("OpenRouterClient: models list must not be empty")

        lead_model = self.models[0]
        safe_tokens = _safe_max_tokens(lead_model, max_tokens)

        payload: dict = {
            "models": self.models,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.1,
            "max_tokens": safe_tokens,
        }
        # Best-effort structured-output hint. NEVER add require_parameters.
        if response_format is not None:
            payload["response_format"] = response_format

        data = await self._post_chat(payload)

        # OpenRouter returns the actually-served model here (may differ from lead).
        served_model = data.get("model", lead_model)

        try:
            choice = data["choices"][0]
            content = choice["message"].get("content") or ""
        except (KeyError, IndexError, TypeError):
            return None, served_model

        if not content or not content.strip():
            # Common with thinking models under-budgeted, or upstream content
            # filtering. Caller decides what to do with a None result.
            return None, served_model

        parsed = _parse_json_tolerant(content)
        return parsed, served_model
