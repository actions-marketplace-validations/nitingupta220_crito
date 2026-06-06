"""
OpenRouter LLM Gateway Service
Sends prompts to any model via the OpenRouter unified API.

Spike-validated changes (2026-06-02):
- models[] fallback array (max 3) — the single biggest reliability lever
- require_parameters NEVER sent — kills entire free pool
- Hardened JSON parser: strip fences, strict=False fallback, empty-content guard
- Thinking-model guard: GLM / Kimi need generous max_tokens or they return empty
- Actual served model logged via response.model field
"""
import json
import re
import time
from dataclasses import dataclass
from typing import Optional

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings

log = structlog.get_logger()

# Models with internal reasoning/thinking that consume extra tokens.
# Must be given a larger output budget so visible content isn't empty.
_THINKING_MODEL_PATTERNS = ("glm", "kimi", "o1", "o3", "deepseek-r1")
_THINKING_MIN_TOKENS = 4096


def _is_thinking_model(model_id: str) -> bool:
    return any(p in model_id.lower() for p in _THINKING_MODEL_PATTERNS)


def _safe_max_tokens(model_id: str, requested: int) -> int:
    """Ensure thinking models get enough output budget."""
    if _is_thinking_model(model_id) and requested < _THINKING_MIN_TOKENS:
        log.debug(
            "Raising max_tokens for thinking model",
            model=model_id,
            from_=requested,
            to=_THINKING_MIN_TOKENS,
        )
        return _THINKING_MIN_TOKENS
    return requested


def _strip_json_fences(text: str) -> str:
    """Strip ```json ... ``` and ``` ... ``` fences from LLM output."""
    text = text.strip()
    # Remove opening fence with optional language tag
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    # Remove closing fence
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


def _parse_json_tolerant(raw: str) -> Optional[dict]:
    """
    Try to parse JSON with increasing tolerance.
    Returns None if all attempts fail.
    """
    if not raw or not raw.strip():
        return None

    # Attempt 1: strict parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Attempt 2: strip fences and retry
    cleaned = _strip_json_fences(raw)
    if cleaned != raw:
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

    # Attempt 3: non-strict (tolerates control chars in strings)
    try:
        return json.loads(cleaned, strict=False)
    except json.JSONDecodeError:
        pass

    # Attempt 4: find first JSON object via brace matching
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        snippet = cleaned[start : end + 1]
        try:
            return json.loads(snippet, strict=False)
        except json.JSONDecodeError:
            pass

    return None


@dataclass
class LLMResponse:
    content: str
    model: str           # actual served model (may differ from requested)
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    latency_ms: int


class OpenRouterService:
    def __init__(self):
        self.base_url = settings.openrouter_base_url
        self.headers = {
            "Authorization": f"Bearer {settings.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/ai-pr-review-assistant",
            "X-Title": "AI PR Review Assistant",
        }

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def chat(
        self,
        models: list[str],
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        response_format: Optional[dict] = None,
    ) -> LLMResponse:
        """
        Send a chat completion request to OpenRouter using the models[] fallback array.

        Args:
            models: Up to 3 model IDs in priority order. OpenRouter auto-routes
                    around 429s, 404s, and context-length errors.
            response_format: Optional format hint (best-effort, NOT enforced on all
                             free models — never send require_parameters:true).
        """
        if not models:
            raise ValueError("models list must not be empty")
        if len(models) > 3:
            log.warning("models[] capped at 3 — truncating", supplied=len(models))
            models = models[:3]

        # Ensure thinking models have adequate output budget
        lead_model = models[0]
        safe_tokens = _safe_max_tokens(lead_model, max_tokens)

        payload: dict = {
            "models": models,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": safe_tokens,
        }

        # Add response_format best-effort — helps JSON-compliant models,
        # silently ignored by others. NEVER add require_parameters here.
        if response_format:
            payload["response_format"] = response_format

        start_time = time.monotonic()

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers=self.headers,
                json=payload,
            )
            response.raise_for_status()

        latency_ms = int((time.monotonic() - start_time) * 1000)
        data = response.json()

        usage = data.get("usage", {})
        choice = data["choices"][0]
        content = choice["message"].get("content") or ""
        # OpenRouter returns the actual served model here
        model_used = data.get("model", lead_model)

        log.info(
            "LLM response received",
            requested_models=models,
            served_model=model_used,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            latency_ms=latency_ms,
            content_len=len(content),
        )

        return LLMResponse(
            content=content,
            model=model_used,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
            latency_ms=latency_ms,
        )

    async def chat_json(
        self,
        models: list[str],
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> tuple[dict, LLMResponse]:
        """
        Like chat(), but parses the response as JSON with full defensive parsing.

        Strategy:
        1. Send json_schema response_format as a hint (best-effort, not enforced).
        2. Strip markdown fences from response.
        3. Try json.loads() strict → non-strict → brace-extraction.
        4. On empty content or total parse failure, return an error dict.
        5. Do NOT re-prompt on failure here — callers handle retry logic.

        Returns: (parsed_dict, llm_response)
        """
        response = await self.chat(
            models=models,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            # Best-effort JSON hint — no strict enforcement
            response_format={"type": "json_object"},
        )

        content = response.content

        # Guard: empty content (common with thinking models under-budgeted)
        if not content or not content.strip():
            log.warning(
                "Empty LLM response — thinking model token budget too low?",
                model=response.model,
                max_tokens=max_tokens,
            )
            return {"error": "empty_response", "raw": ""}, response

        parsed = _parse_json_tolerant(content)

        if parsed is None:
            log.warning(
                "Failed to parse LLM JSON output after all attempts",
                model=response.model,
                raw_preview=content[:300],
            )
            return {"error": "json_parse_failed", "raw": content[:500]}, response

        return parsed, response

    async def get_quota(self) -> dict:
        """
        Check current OpenRouter key quota/usage via GET /api/v1/key.
        Returns the raw response dict. Useful for observability.
        Note: free-model daily counter is NOT exposed here (only USD usage).
        """
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{self.base_url}/key",
                headers=self.headers,
            )
            response.raise_for_status()
            return response.json()


# Singleton instance
openrouter_service = OpenRouterService()
