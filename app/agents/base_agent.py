"""
Base Agent
All specialized agents inherit from this class. Provides shared
LLM calling logic, structured output parsing, and error handling.

Key changes from research:
- _get_models() returns list[str] for models[] fallback array
- Anti-false-positive directive injected into every user prompt
- Uses openrouter_service.chat_json() with models list
"""
import sys
import time
from dataclasses import dataclass, field
from abc import ABC, abstractmethod

import structlog

from app.services.github_service import PRData
from app.services.openrouter_service import openrouter_service

log = structlog.get_logger()

# Injected into every agent's user prompt — proven to cut false positives.
# Source: research finding #4, PR-Agent prompt engineering.
ANTI_FALSE_POSITIVE_DIRECTIVE = """
---
REVIEW RULES (follow strictly):
- Review ONLY lines prefixed with `+` (additions). Do NOT flag deleted or context lines.
- Each finding MUST be discrete and actionable with a specific file/code reference.
- Do NOT speculate that a change might break other code unless you can identify the exact affected path.
- Prefer reporting nothing over guessing. If you are not confident, omit the finding.
- Do not report style-only nits as high/critical severity.
- Do not re-flag issues already handled by the diff (e.g., a deleted bad line is already fixed).
Return ONLY the JSON object — no markdown fences, no explanation outside JSON.
"""


@dataclass
class AgentResult:
    agent_name: str
    model_used: str
    output: dict                    # Parsed JSON from agent
    raw_content: str                # Raw LLM text (for debugging)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: int = 0
    error: str | None = None
    findings: list[dict] = field(default_factory=list)


class BaseAgent(ABC):
    """Abstract base class for all review agents."""

    name: str = "base"

    @abstractmethod
    def _get_models(self) -> list[str]:
        """Return the ordered model fallback list (max 3) for this agent."""
        ...

    @abstractmethod
    def _build_user_prompt(self, pr_data: PRData) -> str:
        """Build the user-facing prompt from PR data."""
        ...

    @property
    def _system_prompt(self) -> str:
        """Return the agent's system prompt.

        Looks up the ``SYSTEM_PROMPT`` module-level variable in the concrete
        subclass's own module, falling back to a generic reviewer prompt.
        """
        module = sys.modules.get(self.__class__.__module__)
        return getattr(module, "SYSTEM_PROMPT", "You are a helpful code reviewer.")

    async def _run_agent(self, pr_data: PRData) -> AgentResult:
        """Execute the agent against the given PR data."""
        start = time.monotonic()
        models = self._get_models()
        user_prompt = self._build_user_prompt(pr_data) + ANTI_FALSE_POSITIVE_DIRECTIVE

        log.info(
            "Agent starting",
            agent=self.name,
            models=models,
            repo=pr_data.repo,
            pr=pr_data.pr_number,
        )

        try:
            parsed, llm_resp = await openrouter_service.chat_json(
                models=models,
                system_prompt=self._system_prompt,
                user_prompt=user_prompt,
                temperature=0.1,
                max_tokens=4096,
            )

            findings = parsed.get("findings", [])
            log.info(
                "Agent complete",
                agent=self.name,
                served_model=llm_resp.model,
                findings=len(findings),
                tokens=llm_resp.total_tokens,
                latency_ms=llm_resp.latency_ms,
            )

            return AgentResult(
                agent_name=self.name,
                model_used=llm_resp.model,
                output=parsed,
                raw_content=llm_resp.content,
                prompt_tokens=llm_resp.prompt_tokens,
                completion_tokens=llm_resp.completion_tokens,
                total_tokens=llm_resp.total_tokens,
                latency_ms=llm_resp.latency_ms,
                findings=findings,
            )

        except Exception as exc:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            log.error("Agent failed", agent=self.name, error=str(exc))
            return AgentResult(
                agent_name=self.name,
                model_used=models[0] if models else "unknown",
                output={"error": str(exc)},
                raw_content="",
                latency_ms=elapsed_ms,
                error=str(exc),
            )
