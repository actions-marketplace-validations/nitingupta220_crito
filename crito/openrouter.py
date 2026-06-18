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
import asyncio
import json
import random
import re
from typing import Optional

import httpx

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

MAX_MODELS = 3

# Typed errors live in the stdlib-only crito.errors module so the ensemble can
# import them without pulling in httpx. Re-exported here for callers of the client.
from crito.errors import OpenRouterError, ModelUnavailable, KeyFatal  # noqa: E402,F401

# Models with internal reasoning/thinking that consume extra output tokens.
# They must be given a larger output budget or visible content comes back empty.
# Includes the top free CODING models (laguna/north/nex) + nemotron — all are
# reasoning models that would otherwise return empty content under the bare floor.
_THINKING_MODEL_PATTERNS = (
    "glm", "kimi", "o1", "o3", "deepseek-r1",
    "laguna", "poolside", "north", "nex", "nemotron",
)
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
            "HTTP-Referer": "https://github.com/crito",
            "X-Title": "crito",
        }

    # -- internal HTTP -------------------------------------------------------

    async def _post_chat(self, payload: dict) -> dict:
        """
        POST one chat-completion request with bounded backoff on 429/503.

        Raises a TYPED error so the ensemble can react:
          * 401 / 402  -> ``KeyFatal``       (key dead — abort the whole run)
          * 404 / 403 / 400 / other 4xx-5xx, and 429/503 after the retry cap,
            and network errors after the cap -> ``ModelUnavailable`` (advance to
            the next model in the ranked pool).
        429 / 503 are retried in place with exponential backoff + jitter
        (honoring ``Retry-After``) up to ``_MAX_RETRIES`` before being surfaced.
        """
        async with httpx.AsyncClient(timeout=self.timeout) as http:
            for attempt in range(_MAX_RETRIES):
                try:
                    resp = await http.post(
                        f"{self.BASE_URL}/chat/completions",
                        headers=self.headers,
                        json=payload,
                    )
                except httpx.RequestError as exc:
                    # Network-level error (DNS, connect, read timeout). Transient:
                    # back off and retry, then treat as unavailable -> advance.
                    if attempt >= _MAX_RETRIES - 1:
                        raise ModelUnavailable(
                            f"network error: {type(exc).__name__}"
                        ) from exc
                    await asyncio.sleep(self._backoff_delay(attempt))
                    continue

                sc = resp.status_code
                # Key-level failures: advancing models cannot help.
                if sc in (401, 402):
                    raise KeyFatal(f"OpenRouter auth/billing error (HTTP {sc})")
                # Transient throttling/unavailability: retry in place, then advance.
                if sc in _RETRYABLE_STATUS and attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(self._backoff_delay(attempt, resp))
                    continue
                # Any other 4xx/5xx (404 dead slug, 403, 400 bad id, or a 429/503
                # that survived the retry cap): this model can't serve -> advance.
                if sc >= 400:
                    raise ModelUnavailable(f"HTTP {sc} for {payload.get('models')}")
                return resp.json()

        raise ModelUnavailable("exhausted retries")

    @staticmethod
    def _backoff_delay(attempt: int, resp: "Optional[httpx.Response]" = None) -> float:
        """Compute an exponential-backoff delay (seconds) with jitter.

        Honors a ``Retry-After`` header when present. Returns the delay so the
        async caller can ``await asyncio.sleep`` on it — we must never block the
        event loop with ``time.sleep`` here, since the ensemble fans several of
        these requests out concurrently.
        """
        delay = min(_BACKOFF_CAP, _BACKOFF_BASE * (2 ** attempt))
        if resp is not None:
            retry_after = resp.headers.get("retry-after")
            if retry_after:
                try:
                    delay = min(_BACKOFF_CAP, float(retry_after))
                except (TypeError, ValueError):
                    pass
        return delay + random.uniform(0, delay * 0.25)  # jitter

    # -- public API ----------------------------------------------------------

    async def chat_json(
        self,
        system: str,
        user: str,
        max_tokens=None,
        response_format=None,
        models=None,
    ) -> tuple:
        """
        Send (system, user) to the models[] array and return parsed JSON.

        Returns:
            (parsed_obj_or_None, served_model_id)

        - Uses ``models`` when given (capped at <=3), else the client's default
          array. Passing it explicitly lets concurrent callers (the ensemble)
          pin a single model per call WITHOUT mutating shared client state.
        - response_format is forwarded best-effort (a hint). require_parameters
          is NEVER sent.
        - Output budget is clamped via _safe_max_tokens (4096 floor, thinking-model
          bump) so glm/kimi don't return empty content.
        - Returns (None, served_model) on empty content or unrecoverable JSON.
        """
        selected = list(models)[:MAX_MODELS] if models else self.models
        if not selected:
            raise ValueError("OpenRouterClient: models list must not be empty")

        lead_model = selected[0]
        safe_tokens = _safe_max_tokens(lead_model, max_tokens)

        payload: dict = {
            "models": selected,
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

    async def list_free_models(self) -> set:
        """Fetch the live ``:free`` model catalog (one GET, best-effort).

        Returns the set of ``:free`` model ids OpenRouter currently lists, so a
        retired slug (e.g. a model that lost its free tier) can be pruned from
        the ranked pool BEFORE a run rather than wasting a failover slot on a
        guaranteed 404. On any error returns an empty set — the caller then keeps
        the full pool and relies on reactive per-slot failover.
        """
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as http:
                resp = await http.get(
                    f"{self.BASE_URL}/models", headers=self.headers
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            return set()
        out = set()
        for m in (data.get("data") or []):
            mid = m.get("id")
            if isinstance(mid, str) and mid.endswith(":free"):
                out.add(mid)
        return out


def prune_to_catalog(pool: list, catalog: set) -> list:
    """Keep only pool ids present in the live ``catalog``, preserving rank order.

    If ``catalog`` is empty (the fetch failed) the pool is returned unchanged so
    the run still proceeds and falls back on reactive per-slot failover.
    """
    if not catalog:
        return list(pool)
    return [m for m in pool if m in catalog]
