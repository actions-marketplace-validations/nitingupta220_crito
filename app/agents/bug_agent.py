"""
Bug Detection Agent
Analyzes PR diffs for logic errors, null dereferences, off-by-one errors,
race conditions, resource leaks, and incorrect assumptions.
"""
import structlog

from app.agents.base_agent import AgentResult, BaseAgent
from app.config import settings
from app.services.github_service import PRData

log = structlog.get_logger()

SYSTEM_PROMPT = """You are an expert software engineer specializing in identifying bugs and logic errors in code.

Focus on:
1. **Logic Errors** — incorrect conditions, wrong operator (== vs is, & vs &&), inverted logic
2. **Null / None Dereferences** — accessing attributes on potentially null values
3. **Off-by-One Errors** — incorrect loop bounds, index errors
4. **Race Conditions** — shared state in concurrent code without proper locks
5. **Resource Leaks** — unclosed files, DB connections, network sockets
6. **Error Handling** — swallowed exceptions, missing try/catch, wrong exception types
7. **Async Bugs** — missing await, blocking calls in async context
8. **Type Mismatches** — incorrect type assumptions, coercion issues
9. **Infinite Loops / Recursion** — missing base case, wrong loop condition

You MUST respond with ONLY a valid JSON object — no markdown, no explanation outside JSON.

JSON schema:
{
  "summary": "Brief overall bug assessment (1–2 sentences)",
  "risk_level": "critical|high|medium|low|none",
  "findings": [
    {
      "severity": "critical|high|medium|low|info",
      "category": "logic|null_deref|off_by_one|race_condition|resource_leak|error_handling|async|type|infinite_loop|other",
      "title": "Short bug title",
      "description": "What the bug is and why it's problematic",
      "file": "filename or null",
      "line_hint": "approximate line or code snippet",
      "suggestion": "How to fix the bug"
    }
  ],
  "positive_observations": ["Any good coding practices noticed"]
}

If no bugs found, return an empty findings array with risk_level "none".
"""


class BugAgent(BaseAgent):
    name = "bug_detection"

    def _get_models(self) -> list[str]:
        return settings.get_model_list(settings.bug_models)

    def _build_user_prompt(self, pr_data: PRData) -> str:
        files_summary = "\n".join(
            f"- {f.filename} ({f.language or 'unknown'}) +{f.additions}/-{f.deletions}"
            for f in pr_data.files
        )
        return f"""## Pull Request: {pr_data.title}
**Repository:** {pr_data.repo}
**Author:** {pr_data.author}
**PR Description:** {pr_data.body or 'No description provided'}

## Changed Files ({len(pr_data.files)} files)
{files_summary}

## Full Diff
```diff
{pr_data.full_diff[:30000]}
```

Analyze this diff for bugs, logic errors, and runtime issues. Return ONLY the JSON object."""

    async def run(self, pr_data: PRData) -> AgentResult:
        return await self._run_agent(pr_data)
