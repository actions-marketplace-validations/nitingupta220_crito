"""
Webhook Router
Receives GitHub webhook events and dispatches to the review pipeline.

Security notes:
- HMAC-SHA256 signature verification before any processing
- X-GitHub-Delivery idempotency (bounded in-memory set, last 1000)
- pull_request (NOT pull_request_target) — no pwn-request risk
- ready_for_review added (draft → review-ready conversion)
"""
import hashlib
import hmac
import json
import time
from collections import OrderedDict

import structlog
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request

from app.config import settings
from app.services.pipeline_service import review_pipeline

log = structlog.get_logger()
router = APIRouter()

# ── Delivery ID idempotency cache ────────────────────────────────────────────
# Bounded LRU-style dict: stores (delivery_id -> timestamp).
# Prevents duplicate reviews from webhook redeliveries.
_DELIVERY_CACHE: OrderedDict[str, float] = OrderedDict()
_DELIVERY_CACHE_MAX = 1000
_DELIVERY_TTL_SEC = 3600  # 1 hour


def _seen_delivery(delivery_id: str) -> bool:
    """Return True if this delivery was already processed (dedup)."""
    now = time.monotonic()
    if delivery_id in _DELIVERY_CACHE:
        ts = _DELIVERY_CACHE[delivery_id]
        if now - ts < _DELIVERY_TTL_SEC:
            return True
        # Expired — remove and reprocess
        del _DELIVERY_CACHE[delivery_id]

    # Record as seen
    _DELIVERY_CACHE[delivery_id] = now
    # Evict oldest if over cap
    while len(_DELIVERY_CACHE) > _DELIVERY_CACHE_MAX:
        _DELIVERY_CACHE.popitem(last=False)
    return False


def verify_github_signature(payload: bytes, signature: str) -> bool:
    """Verify GitHub webhook HMAC-SHA256 signature."""
    expected = "sha256=" + hmac.new(
        settings.github_webhook_secret.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


async def process_pr_review(payload: dict):
    """Background task — runs the full review pipeline."""
    repo = payload["repository"]["full_name"]
    pr_number = payload["pull_request"]["number"]
    pr_title = payload["pull_request"]["title"]
    pr_author = payload["pull_request"]["user"]["login"]
    is_draft = payload["pull_request"].get("draft", False)

    if is_draft:
        log.info("Skipping draft PR", repo=repo, pr_number=pr_number)
        return

    log.info(
        "PR review started",
        repo=repo,
        pr_number=pr_number,
        title=pr_title,
        author=pr_author,
    )

    try:
        result = await review_pipeline.run(repo=repo, pr_number=pr_number)
        log.info(
            "PR review pipeline finished",
            repo=repo,
            pr_number=pr_number,
            verdict=result.get("verdict"),
            total_findings=result.get("total_findings"),
            elapsed_ms=result.get("elapsed_ms"),
        )
    except Exception as exc:
        log.error("PR review pipeline crashed", repo=repo, pr=pr_number, error=str(exc))


@router.post("/github")
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: str = Header(None),
    x_github_event: str = Header(None),
    x_github_delivery: str = Header(None),
):
    payload_bytes = await request.body()

    # 1. Verify signature
    if not x_hub_signature_256 or not verify_github_signature(payload_bytes, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    # 2. Idempotency check — deduplicate redeliveries
    if x_github_delivery and _seen_delivery(x_github_delivery):
        log.info("Duplicate webhook delivery ignored", delivery_id=x_github_delivery)
        return {"status": "ignored", "reason": "duplicate_delivery"}

    payload = json.loads(payload_bytes)
    action = payload.get("action", "")

    log.info("GitHub event received", event=x_github_event, action=action)

    # 3. Process PR events — opened, synchronize, reopened, ready_for_review
    if x_github_event == "pull_request" and action in (
        "opened", "synchronize", "reopened", "ready_for_review"
    ):
        background_tasks.add_task(process_pr_review, payload)
        return {
            "status": "accepted",
            "message": f"PR #{payload['pull_request']['number']} queued for review",
        }

    # 4. /review comment re-trigger (maintainer-gated — for fork PRs)
    if (
        x_github_event == "issue_comment"
        and action == "created"
        and "/review" in payload.get("comment", {}).get("body", "")
        and payload.get("issue", {}).get("pull_request")  # it's a PR, not a plain issue
    ):
        pr_number = payload["issue"]["number"]
        repo = payload["repository"]["full_name"]
        log.info("/review comment trigger", repo=repo, pr=pr_number)
        background_tasks.add_task(
            process_pr_review,
            {
                "repository": {"full_name": repo},
                "pull_request": {
                    "number": pr_number,
                    "title": payload["issue"]["title"],
                    "user": {"login": payload["issue"]["user"]["login"]},
                    "draft": False,
                },
            },
        )
        return {"status": "accepted", "message": f"PR #{pr_number} queued for /review"}

    return {"status": "ignored", "reason": f"Event '{x_github_event}:{action}' not handled"}
