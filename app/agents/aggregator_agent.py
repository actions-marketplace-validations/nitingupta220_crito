"""
Aggregator Agent
Takes the outputs from all specialized agents and produces a single,
coherent, well-formatted PR review comment ready to post on GitHub.
"""
import json
import time

import structlog

from app.agents.base_agent import AgentResult, BaseAgent
from app.config import settings
from app.services.github_service import PRData
from app.services.openrouter_service import openrouter_service

log = structlog.get_logger()

SYSTEM_PROMPT = """You are a Principal Engineer writing a final PR review based on reports from specialist agents.

Your job is to synthesize multiple agent reports into one clear, actionable GitHub PR review comment.

Guidelines:
- Be constructive and respectful — this is feedback for a developer
- Group findings by severity (Critical → High → Medium → Low → Info)
- Avoid duplicates — merge overlapping findings from different agents
- Be specific — reference filenames and line hints where available
- Highlight positives too — acknowledge good practices
- Use GitHub-flavored markdown for formatting
- Keep it scannable with headers, bullet points, and emoji

You MUST respond with ONLY a valid JSON object.

JSON schema:
{
  "overall_verdict": "approve|request_changes|comment",
  "overall_risk": "critical|high|medium|low|none",
  "summary": "2-3 sentence executive summary of the PR quality",
  "github_comment": "The full GitHub-flavored markdown comment to post (use \\n for newlines)",
  "total_findings": 0,
  "findings_by_severity": {
    "critical": 0,
    "high": 0,
    "medium": 0,
    "low": 0,
    "info": 0
  },
  "must_fix": ["List of blocking issues that MUST be fixed before merge"],
  "should_fix": ["List of important issues that should be fixed"],
  "nice_to_fix": ["Optional improvements"]
}
"""


class AggregatorAgent(BaseAgent):
    name = "aggregator"

    def _get_models(self) -> list[str]:
        return settings.get_model_list(settings.aggregator_models)

    def _build_user_prompt(self, pr_data: PRData) -> str:
        # This is overridden — aggregator takes agent results, not raw PR data
        return ""

    def _build_aggregator_prompt(
        self,
        pr_data: PRData,
        agent_results: list[AgentResult],
        static_analysis_results: dict,
    ) -> str:
        agent_reports = []
        for result in agent_results:
            if result.error:
                agent_reports.append(f"### {result.agent_name.upper()} AGENT\nERROR: {result.error}")
            else:
                agent_reports.append(
                    f"### {result.agent_name.upper()} AGENT (model: {result.model_used})\n"
                    f"```json\n{json.dumps(result.output, indent=2)[:3000]}\n```"
                )

        static_section = ""
        if static_analysis_results:
            static_section = f"""
## Static Analysis Results
```json
{json.dumps(static_analysis_results, indent=2)[:2000]}
```
"""

        return f"""## Pull Request to Review

**Title:** {pr_data.title}
**Repository:** {pr_data.repo}
**Author:** {pr_data.author}
**Branch:** {pr_data.head_branch} → {pr_data.base_branch}
**Changes:** {len(pr_data.files)} files, +{pr_data.total_additions} / -{pr_data.total_deletions}
**PR URL:** {pr_data.pr_url}

---

## Agent Reports

{"---".join(agent_reports)}

{static_section}

---

Based on all the above agent reports, synthesize a final comprehensive PR review.
Create a well-formatted GitHub comment that a developer will receive.
The comment should use GitHub-flavored markdown with emoji, headers, and bullet points.
Return ONLY the JSON object."""

    async def run(self, pr_data: PRData) -> AgentResult:
        # Standard run is not used — use run_aggregation instead
        return await self._run_agent(pr_data)

    async def run_aggregation(
        self,
        pr_data: PRData,
        agent_results: list[AgentResult],
        static_analysis_results: dict,
    ) -> AgentResult:
        """Run the aggregator with all agent outputs."""
        start = time.monotonic()
        models = self._get_models()
        user_prompt = self._build_aggregator_prompt(pr_data, agent_results, static_analysis_results)

        log.info("Aggregator starting", models=models, repo=pr_data.repo, pr=pr_data.pr_number)

        try:
            parsed, llm_resp = await openrouter_service.chat_json(
                models=models,
                system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt,
                temperature=0.2,
                max_tokens=8192,
            )

            log.info(
                "Aggregator complete",
                verdict=parsed.get("overall_verdict"),
                risk=parsed.get("overall_risk"),
                total_findings=parsed.get("total_findings"),
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
            )

        except Exception as exc:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            log.error("Aggregator failed", error=str(exc))
            return AgentResult(
                agent_name=self.name,
                model_used=models[0] if models else "unknown",
                output={"error": str(exc)},
                raw_content="",
                latency_ms=elapsed_ms,
                error=str(exc),
            )


# Singleton instance
aggregator_agent = AggregatorAgent()
