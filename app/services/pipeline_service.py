"""
Review Pipeline Orchestrator
The central coordinator that:
1. Fetches PR data from GitHub
2. Checks diff hash — skips if unchanged (dedup)
3. Runs all 5 specialist agents in parallel (asyncio.gather)
4. Runs static analysis in a thread pool (blocking subprocess calls)
5. Runs the aggregator agent to synthesize results
6. Posts the final review comment back to GitHub
7. Persists results to the database (if available)
"""
import asyncio
import hashlib
import time
from concurrent.futures import ThreadPoolExecutor

import structlog

from app.agents.aggregator_agent import aggregator_agent
from app.agents.base_agent import AgentResult
from app.agents.bug_agent import BugAgent
from app.agents.docs_agent import DocumentationAgent
from app.agents.performance_agent import PerformanceAgent
from app.agents.quality_agent import QualityAgent
from app.agents.security_agent import SecurityAgent
from app.services.github_service import PRData, github_service
from app.services.static_analysis_service import static_analysis_service
from app.database import AsyncSessionLocal
from app.models.db_models import (
    AgentOutput,
    Finding,
    PullRequest,
    Review,
    ReviewStatus,
    Severity,
)
from sqlalchemy import select

log = structlog.get_logger()

# Thread pool for blocking static analysis subprocess calls
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="static_analysis")

# In-flight guard: one active pipeline per repo+PR at a time
_in_flight_locks: dict[str, asyncio.Lock] = {}
_in_flight_lock_registry = asyncio.Lock()

# Diff hash cache: maps "repo/pr_number" -> last reviewed diff hash
# Prevents re-reviewing identical diffs on repeated synchronize events
_diff_hash_cache: dict[str, str] = {}


def _format_fallback_comment(
    pr_data: PRData,
    agent_results: list[AgentResult],
    static_result: dict,
    error: str | None = None,
) -> str:
    """Emergency fallback: build a markdown comment manually if aggregator fails."""
    lines = [
        "## 🤖 AI PR Review Assistant",
        "",
        f"**PR:** {pr_data.title}",
        f"**Repo:** {pr_data.repo} | **Author:** @{pr_data.author}",
        "",
    ]

    if error:
        lines += [
            "⚠️ **Aggregator failed** — showing raw agent findings below.",
            "",
        ]

    all_findings = []
    for result in agent_results:
        if result.error:
            lines.append(f"- ❌ `{result.agent_name}` agent failed: {result.error}")
        else:
            for f in result.findings:
                all_findings.append((result.agent_name, f))

    if all_findings:
        lines += ["", "### Findings", ""]
        for agent_name, finding in sorted(
            all_findings,
            key=lambda x: {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}.get(
                x[1].get("severity", "info"), 5
            ),
        ):
            sev = finding.get("severity", "info")
            emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵", "info": "⚪"}.get(sev, "⚪")
            lines.append(
                f"- {emoji} **[{sev.upper()}]** `{finding.get('title', 'Issue')}` "
                f"— *{agent_name}* "
                f"({finding.get('file', '')})"
            )
    else:
        lines.append("✅ No significant issues found.")

    lines += [
        "",
        "---",
        "*Powered by [AI PR Review Assistant](https://github.com/ai-pr-review-assistant)*",
    ]
    return "\n".join(lines)


class ReviewPipeline:
    """Orchestrates the full PR review pipeline."""

    def __init__(self):
        self._agents = [
            SecurityAgent(),
            BugAgent(),
            PerformanceAgent(),
            QualityAgent(),
            DocumentationAgent(),
        ]

    async def run(self, repo: str, pr_number: int) -> dict:
        """
        Full pipeline execution.
        Returns a summary dict with all results and the posted comment ID.
        """
        pr_key = f"{repo}/{pr_number}"

        # ── In-flight concurrency guard ───────────────────────────────────────
        async with _in_flight_lock_registry:
            if pr_key not in _in_flight_locks:
                _in_flight_locks[pr_key] = asyncio.Lock()
        pr_lock = _in_flight_locks[pr_key]

        if pr_lock.locked():
            log.warning("Pipeline already in-flight — skipping", repo=repo, pr=pr_number)
            return {"status": "skipped", "reason": "already_in_flight"}

        async with pr_lock:
            return await self._run_pipeline(repo=repo, pr_number=pr_number, pr_key=pr_key)

    async def _run_pipeline(self, repo: str, pr_number: int, pr_key: str) -> dict:
        """Internal pipeline execution (called under the per-PR lock)."""
        pipeline_start = time.monotonic()
        log.info("Pipeline starting", repo=repo, pr=pr_number)

        # ── Step 1: Fetch PR data ────────────────────────────────────────────
        try:
            pr_data: PRData = await github_service.fetch_pr_data(repo, pr_number)
        except Exception as exc:
            log.error("Failed to fetch PR data", repo=repo, pr=pr_number, error=str(exc))
            return {"status": "failed", "error": f"GitHub fetch failed: {exc}"}

        # ── Step 2: Diff-hash dedup ──────────────────────────────────────────
        diff_hash = hashlib.sha256(pr_data.full_diff.encode()).hexdigest()[:16]
        if _diff_hash_cache.get(pr_key) == diff_hash:
            log.info("Diff unchanged — skipping review", repo=repo, pr=pr_number)
            return {"status": "skipped", "reason": "diff_unchanged", "diff_hash": diff_hash}
        _diff_hash_cache[pr_key] = diff_hash

        # ── Step 3: Run all 5 agents in parallel ────────────────────────────
        log.info("Running specialist agents in parallel", count=len(self._agents))
        agent_tasks = [agent.run(pr_data) for agent in self._agents]
        agent_results: list[AgentResult] = await asyncio.gather(*agent_tasks)

        # ── Step 4: Static analysis (blocking — run in thread pool) ─────────
        log.info("Running static analysis")
        loop = asyncio.get_running_loop()
        try:
            static_result_obj = await loop.run_in_executor(
                _executor,
                static_analysis_service.analyze_pr_files,
                pr_data.files,
            )
            static_result = static_result_obj.to_dict()
        except Exception as exc:
            log.warning("Static analysis failed", error=str(exc))
            static_result = {"error": str(exc), "findings": [], "tools_run": []}

        # ── Step 5: Aggregator ───────────────────────────────────────────────
        log.info("Running aggregator agent")
        aggregation = await aggregator_agent.run_aggregation(pr_data, agent_results, static_result)

        # ── Step 6: Format & post comment ───────────────────────────────────
        if aggregation.error or not aggregation.output.get("github_comment"):
            log.warning("Aggregator failed or missing comment — using fallback formatter")
            comment_body = _format_fallback_comment(
                pr_data, agent_results, static_result, error=aggregation.error
            )
        else:
            comment_body = aggregation.output["github_comment"]

        comment_id = None
        try:
            comment_id = await github_service.post_review_comment(repo, pr_number, comment_body)
            log.info("Posted review comment", repo=repo, pr=pr_number, comment_id=comment_id)
        except Exception as exc:
            log.error("Failed to post GitHub comment", error=str(exc))

        # ── Step 7: Persist to DB (best-effort) ─────────────────────────────
        await self._persist_results(
            pr_data=pr_data,
            agent_results=agent_results,
            aggregation=aggregation,
            static_result=static_result,
            comment_id=comment_id,
        )

        elapsed_ms = int((time.monotonic() - pipeline_start) * 1000)
        log.info(
            "Pipeline complete",
            repo=repo,
            pr=pr_number,
            elapsed_ms=elapsed_ms,
            verdict=aggregation.output.get("overall_verdict"),
            risk=aggregation.output.get("overall_risk"),
            total_findings=aggregation.output.get("total_findings"),
        )

        return {
            "status": "completed",
            "repo": repo,
            "pr_number": pr_number,
            "pr_title": pr_data.title,
            "verdict": aggregation.output.get("overall_verdict"),
            "overall_risk": aggregation.output.get("overall_risk"),
            "total_findings": aggregation.output.get("total_findings", 0),
            "findings_by_severity": aggregation.output.get("findings_by_severity", {}),
            "comment_id": comment_id,
            "elapsed_ms": elapsed_ms,
            "agent_latencies": {
                r.agent_name: r.latency_ms for r in agent_results
            },
        }

    async def _persist_results(
        self,
        pr_data: PRData,
        agent_results: list[AgentResult],
        aggregation: AgentResult,
        static_result: dict,
        comment_id: int | None,
    ) -> None:
        """Persist all results to the DB — gracefully skips if DB is unavailable."""
        try:
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    # Upsert PullRequest record
                    stmt = select(PullRequest).where(
                        PullRequest.github_pr_id == pr_data.pr_id
                    )
                    existing_pr = (await session.execute(stmt)).scalar_one_or_none()

                    if existing_pr:
                        db_pr = existing_pr
                    else:
                        db_pr = PullRequest(
                            github_pr_id=pr_data.pr_id,
                            repo_full_name=pr_data.repo,
                            pr_number=pr_data.pr_number,
                            pr_title=pr_data.title,
                            pr_author=pr_data.author,
                            base_branch=pr_data.base_branch,
                            head_branch=pr_data.head_branch,
                            pr_url=pr_data.pr_url,
                        )
                        session.add(db_pr)
                        await session.flush()

                    # Create Review record
                    review = Review(
                        pr_id=db_pr.id,
                        status=ReviewStatus.completed,
                        diff_size=len(pr_data.full_diff),
                        github_comment_id=comment_id,
                    )
                    session.add(review)
                    await session.flush()

                    # Store agent outputs
                    for result in agent_results:
                        agent_out = AgentOutput(
                            review_id=review.id,
                            agent_name=result.agent_name,
                            model_used=result.model_used,
                            raw_output=result.output,
                            tokens_used=result.total_tokens,
                            latency_ms=result.latency_ms,
                        )
                        session.add(agent_out)

                    # Store aggregator output
                    agg_out = AgentOutput(
                        review_id=review.id,
                        agent_name="aggregator",
                        model_used=aggregation.model_used,
                        raw_output=aggregation.output,
                        tokens_used=aggregation.total_tokens,
                        latency_ms=aggregation.latency_ms,
                    )
                    session.add(agg_out)

                    # Store findings
                    for result in agent_results:
                        for f in result.findings:
                            try:
                                sev = Severity(f.get("severity", "info"))
                            except ValueError:
                                sev = Severity.info
                            finding = Finding(
                                review_id=review.id,
                                source=result.agent_name,
                                severity=sev,
                                category=f.get("category", "other"),
                                title=f.get("title", "")[:500],
                                description=f.get("description", ""),
                                file_path=f.get("file"),
                                suggestion=f.get("suggestion"),
                            )
                            session.add(finding)

            log.info("Results persisted to DB", repo=pr_data.repo, pr=pr_data.pr_number)

        except Exception as exc:
            log.warning("DB persist skipped (non-fatal)", error=str(exc))


# Singleton instance
review_pipeline = ReviewPipeline()
