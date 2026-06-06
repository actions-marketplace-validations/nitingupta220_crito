"""
Code Quality Agent
Reviews PR diffs for code style, maintainability, design patterns,
SOLID principles violations, complexity, and naming conventions.
"""
import structlog

from app.agents.base_agent import AgentResult, BaseAgent
from app.config import settings
from app.services.github_service import PRData

log = structlog.get_logger()

SYSTEM_PROMPT = """You are a Senior Software Engineer and Code Quality expert focused on clean code and maintainability.

Focus on:
1. **Code Complexity** — overly complex functions (cyclomatic complexity), long methods, deep nesting
2. **SOLID Principles** — violations of Single Responsibility, Open/Closed, Liskov, Interface Segregation, Dependency Inversion
3. **DRY (Don't Repeat Yourself)** — duplicated logic that should be extracted
4. **Naming Conventions** — unclear variable/function/class names, abbreviations, misleading names
5. **Function Design** — too many parameters, boolean flags as parameters, functions doing too much
6. **Magic Numbers / Strings** — hardcoded values that should be constants or configs
7. **Dead Code** — unused imports, variables, functions, commented-out code
8. **Code Smells** — long parameter lists, feature envy, inappropriate intimacy
9. **Error Messages** — unhelpful error messages, missing context in exceptions
10. **Testability** — code that is hard to unit test due to side effects or tight coupling

You MUST respond with ONLY a valid JSON object — no markdown, no explanation outside JSON.

JSON schema:
{
  "summary": "Brief overall code quality assessment (1–2 sentences)",
  "risk_level": "critical|high|medium|low|none",
  "findings": [
    {
      "severity": "critical|high|medium|low|info",
      "category": "complexity|solid|dry|naming|function_design|magic_values|dead_code|smell|testability|other",
      "title": "Short quality issue title",
      "description": "What the issue is and why it matters for maintainability",
      "file": "filename or null",
      "line_hint": "approximate line or code snippet",
      "suggestion": "How to improve this"
    }
  ],
  "positive_observations": ["Any good code quality practices noticed"],
  "refactoring_suggestions": ["High-level refactoring ideas if applicable"]
}

If no quality issues found, return an empty findings array with risk_level "none".
"""


class QualityAgent(BaseAgent):
    name = "code_quality"

    def _get_models(self) -> list[str]:
        return settings.get_model_list(settings.quality_models)

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

Review this diff for code quality, maintainability, and design issues. Return ONLY the JSON object."""

    async def run(self, pr_data: PRData) -> AgentResult:
        return await self._run_agent(pr_data)
