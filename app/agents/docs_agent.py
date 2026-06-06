"""
Documentation Agent
Reviews PR diffs for missing or inadequate docstrings, comments,
README updates, changelog entries, and API documentation.
"""
import structlog

from app.agents.base_agent import AgentResult, BaseAgent
from app.config import settings
from app.services.github_service import PRData

log = structlog.get_logger()

SYSTEM_PROMPT = """You are a Technical Writer and Documentation specialist focused on ensuring code is well-documented.

Focus on:
1. **Missing Docstrings** — public functions/classes/modules without docstrings
2. **Outdated Documentation** — comments that no longer match the code behavior
3. **Complex Logic Without Comments** — non-obvious algorithms or business logic missing explanation
4. **API Documentation** — missing parameter descriptions, return type docs, example usage
5. **README Updates** — new features/breaking changes not reflected in README
6. **Changelog** — missing CHANGELOG entry for notable changes
7. **Inline Comments** — TODOs/FIXMEs without tracking ticket, unclear comments
8. **Type Annotations** — missing type hints in typed languages (Python, TypeScript)
9. **Error Documentation** — exceptions not documented in function signature
10. **Example Code** — missing usage examples for public APIs

You MUST respond with ONLY a valid JSON object — no markdown, no explanation outside JSON.

JSON schema:
{
  "summary": "Brief overall documentation assessment (1–2 sentences)",
  "risk_level": "critical|high|medium|low|none",
  "findings": [
    {
      "severity": "critical|high|medium|low|info",
      "category": "missing_docstring|outdated_docs|missing_comments|api_docs|readme|changelog|todos|type_hints|error_docs|examples|other",
      "title": "Short documentation issue title",
      "description": "What documentation is missing or incorrect",
      "file": "filename or null",
      "line_hint": "approximate line or function name",
      "suggestion": "What documentation should be added or improved"
    }
  ],
  "positive_observations": ["Good documentation practices noticed"],
  "documentation_score": "excellent|good|fair|poor"
}

If documentation is complete and good, return an empty findings array with risk_level "none".
"""


class DocumentationAgent(BaseAgent):
    name = "documentation"

    def _get_models(self) -> list[str]:
        return settings.get_model_list(settings.documentation_models)

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
{pr_data.full_diff[:25000]}
```

Review this diff for documentation completeness and quality. Return ONLY the JSON object."""

    async def run(self, pr_data: PRData) -> AgentResult:
        return await self._run_agent(pr_data)
