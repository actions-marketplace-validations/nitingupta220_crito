"""
Performance Agent
Analyzes PR diffs for performance bottlenecks, N+1 queries, inefficient
algorithms, unnecessary re-renders, memory leaks, and caching opportunities.
"""
import structlog

from app.agents.base_agent import AgentResult, BaseAgent
from app.config import settings
from app.services.github_service import PRData

log = structlog.get_logger()

SYSTEM_PROMPT = """You are a Performance Engineering expert who specializes in identifying performance bottlenecks in code.

Focus on:
1. **N+1 Query Problems** — DB queries inside loops, missing eager loading
2. **Algorithm Complexity** — O(n²) where O(n log n) is possible, nested loops over large datasets
3. **Memory Issues** — loading large datasets into memory, memory leaks, unbounded caches
4. **Unnecessary Computation** — repeated expensive operations, missing memoization
5. **Database** — missing indexes on filtered columns, SELECT *, inefficient joins, lack of pagination
6. **Network** — missing connection pooling, synchronous calls that should be async, chatty APIs
7. **Caching** — missing cache for expensive computations, cache invalidation issues
8. **Frontend** — unnecessary re-renders, large bundle sizes, missing lazy loading (if applicable)
9. **Concurrency** — missed parallelism opportunities, unnecessary serialization

You MUST respond with ONLY a valid JSON object — no markdown, no explanation outside JSON.

JSON schema:
{
  "summary": "Brief overall performance assessment (1–2 sentences)",
  "risk_level": "critical|high|medium|low|none",
  "findings": [
    {
      "severity": "critical|high|medium|low|info",
      "category": "n_plus_one|algorithm|memory|computation|database|network|caching|frontend|concurrency|other",
      "title": "Short performance issue title",
      "description": "What the issue is and what impact it has",
      "file": "filename or null",
      "line_hint": "approximate line or code snippet",
      "suggestion": "How to optimize this"
    }
  ],
  "positive_observations": ["Any good performance practices noticed"]
}

If no performance issues found, return an empty findings array with risk_level "none".
"""


class PerformanceAgent(BaseAgent):
    name = "performance"

    def _get_models(self) -> list[str]:
        return settings.get_model_list(settings.performance_models)

    def _build_user_prompt(self, pr_data: PRData) -> str:
        files_summary = "\n".join(
            f"- {f.filename} ({f.language or 'unknown'}) +{f.additions}/-{f.deletions}"
            for f in pr_data.files
        )
        return f"""## Pull Request: {pr_data.title}
**Repository:** {pr_data.repo}
**Author:** {pr_data.author}
**Branch:** {pr_data.head_branch} → {pr_data.base_branch}
**Total changes:** +{pr_data.total_additions} / -{pr_data.total_deletions}

## Changed Files ({len(pr_data.files)} files)
{files_summary}

## Full Diff
```diff
{pr_data.full_diff[:30000]}
```

Analyze this diff for performance bottlenecks and optimization opportunities. Return ONLY the JSON object."""

    async def run(self, pr_data: PRData) -> AgentResult:
        return await self._run_agent(pr_data)
