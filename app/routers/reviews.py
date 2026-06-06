"""
Reviews Router
REST API endpoints to query stored PR reviews from the database.
"""
import asyncio

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import AsyncSessionLocal
from app.models.db_models import AgentOutput, Finding, PullRequest, Review
from app.services.pipeline_service import review_pipeline

log = structlog.get_logger()
router = APIRouter()


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


@router.get("/", summary="List all reviews")
async def list_reviews(
    limit: int = 20,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """Return a paginated list of reviews, newest first."""
    try:
        stmt = (
            select(Review)
            .options(selectinload(Review.pull_request))
            .order_by(desc(Review.started_at))
            .limit(limit)
            .offset(offset)
        )
        result = await db.execute(stmt)
        reviews = result.scalars().all()

        return {
            "total": len(reviews),
            "reviews": [
                {
                    "id": str(r.id),
                    "status": r.status,
                    "repo": r.pull_request.repo_full_name if r.pull_request else None,
                    "pr_number": r.pull_request.pr_number if r.pull_request else None,
                    "pr_title": r.pull_request.pr_title if r.pull_request else None,
                    "pr_author": r.pull_request.pr_author if r.pull_request else None,
                    "pr_url": r.pull_request.pr_url if r.pull_request else None,
                    "diff_size": r.diff_size,
                    "github_comment_id": r.github_comment_id,
                    "started_at": r.started_at.isoformat() if r.started_at else None,
                    "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                }
                for r in reviews
            ],
        }
    except Exception as exc:
        log.warning("DB query failed", error=str(exc))
        raise HTTPException(status_code=503, detail="Database unavailable")


@router.get("/{review_id}", summary="Get a review by ID")
async def get_review(
    review_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Return full details of a review including agent outputs and findings."""
    try:
        stmt = (
            select(Review)
            .where(Review.id == review_id)
            .options(
                selectinload(Review.pull_request),
                selectinload(Review.agent_outputs),
                selectinload(Review.findings),
            )
        )
        result = await db.execute(stmt)
        review = result.scalar_one_or_none()

        if not review:
            raise HTTPException(status_code=404, detail="Review not found")

        return {
            "id": str(review.id),
            "status": review.status,
            "pull_request": {
                "repo": review.pull_request.repo_full_name,
                "pr_number": review.pull_request.pr_number,
                "title": review.pull_request.pr_title,
                "author": review.pull_request.pr_author,
                "url": review.pull_request.pr_url,
            } if review.pull_request else None,
            "diff_size": review.diff_size,
            "github_comment_id": review.github_comment_id,
            "started_at": review.started_at.isoformat() if review.started_at else None,
            "completed_at": review.completed_at.isoformat() if review.completed_at else None,
            "agent_outputs": [
                {
                    "agent": ao.agent_name,
                    "model": ao.model_used,
                    "tokens": ao.tokens_used,
                    "latency_ms": ao.latency_ms,
                    "output": ao.raw_output,
                }
                for ao in review.agent_outputs
            ],
            "findings": [
                {
                    "id": str(f.id),
                    "source": f.source,
                    "severity": f.severity,
                    "category": f.category,
                    "title": f.title,
                    "description": f.description,
                    "file": f.file_path,
                    "line": f.line_number,
                    "suggestion": f.suggestion,
                }
                for f in review.findings
            ],
        }
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("DB query failed", error=str(exc))
        raise HTTPException(status_code=503, detail="Database unavailable")


@router.post("/trigger", summary="Manually trigger a PR review")
async def trigger_review(body: dict, db: AsyncSession = Depends(get_db)):
    """
    Manually trigger a review without a GitHub webhook.
    Body: { "repo": "owner/name", "pr_number": 42 }

    Immediately creates a pending Review record and returns its ID so the
    frontend can link to /reviews/{review_id} and poll for live status.
    """
    from app.models.db_models import PullRequest, Review, ReviewStatus
    import re

    repo = body.get("repo", "").strip()
    pr_number = body.get("pr_number")

    # ── Validation ────────────────────────────────────────────────────────
    if not repo or not pr_number:
        raise HTTPException(status_code=422, detail="Both 'repo' and 'pr_number' are required.")

    if not re.match(r"^[\w.\-]+/[\w.\-]+$", repo):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid repository format '{repo}'. Expected 'owner/repo' (e.g. facebook/react).",
        )

    try:
        pr_number = int(pr_number)
        if pr_number < 1:
            raise ValueError()
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="PR number must be a positive integer.")

    # ── Create pending stub in DB so we can return a review_id immediately ─
    review_id: str | None = None
    try:
        from sqlalchemy import select as _select

        # Reuse existing PullRequest if repo+pr already seen, else create a stub
        stmt = _select(PullRequest).where(
            PullRequest.repo_full_name == repo,
            PullRequest.pr_number == pr_number,
        )
        db_pr = (await db.execute(stmt)).scalar_one_or_none()

        if not db_pr:
            import uuid as _uuid
            db_pr = PullRequest(
                github_pr_id=0,          # will be updated by the pipeline
                repo_full_name=repo,
                pr_number=pr_number,
                pr_title="Pending…",
                pr_author="unknown",
                base_branch="",
                head_branch="",
                pr_url=f"https://github.com/{repo}/pull/{pr_number}",
            )
            db.add(db_pr)
            await db.flush()

        review = Review(
            pr_id=db_pr.id,
            status=ReviewStatus.pending,
            triggered_by="manual",
            diff_size=0,
        )
        db.add(review)
        await db.flush()
        review_id = str(review.id)
        await db.commit()

    except Exception as exc:
        log.warning("Could not create pending Review stub", error=str(exc))
        await db.rollback()

    # ── Fire pipeline in background ───────────────────────────────────────
    async def _run():
        await review_pipeline.run(repo=repo, pr_number=pr_number)

    asyncio.create_task(_run())

    return {
        "status": "triggered",
        "review_id": review_id,
        "repo": repo,
        "pr_number": pr_number,
        "pr_url": f"https://github.com/{repo}/pull/{pr_number}",
        "message": (
            "Review is running in the background. "
            "Check the Reviews page for live status."
        ),
    }

