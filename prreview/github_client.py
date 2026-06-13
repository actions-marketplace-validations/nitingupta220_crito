"""
GitHub REST API client for the PR review agent.

A thin, synchronous wrapper around the handful of GitHub endpoints the agent
needs. Pure read-and-comment: it fetches PR metadata, the changed-file patches,
and the author's collaborator permission, then posts ONE batched review plus a
sticky summary comment. It NEVER checks out, merges, executes, or otherwise
writes to the repository tree — the only mutations are review/issue comments.

Salvaged from the original ``github_service.py`` (httpx PR-files pagination,
``patch``-may-be-None handling, noise awareness) and reshaped to the GitHubClient
CONTRACT. The original used an async client tangled into FastAPI settings; here
it is a standalone ``httpx.Client`` with explicit constructor args and no global
state.

Depends only on httpx + the Python stdlib. This module imports httpx, so it MUST
NOT be imported by the stdlib-only smoke test.

Auth: ``Authorization: Bearer <token>``. The token is held on the instance and
is NEVER logged, stringified into errors, or echoed back.
"""
from __future__ import annotations

import httpx

# GitHub returns at most this many files for a PR via the files API; beyond it
# the listing is truncated and additional pages just repeat/stop. We stop
# paginating once we have collected this many to avoid spinning on huge PRs.
_MAX_PR_FILES = 3000

_PER_PAGE = 100

# Marker-driven summary comment lookups can page through a lot of comments on a
# busy PR; bound it so we never loop unboundedly.
_MAX_COMMENT_PAGES = 50

_API_VERSION = "2022-11-28"


class GitHubClient:
    """Synchronous GitHub REST client scoped to a single ``owner/repo``.

    Usage::

        gh = GitHubClient(token, owner, repo)
        pr = gh.get_pr(123)
        files = gh.get_pr_files(123)
        perm = gh.get_permission("octocat")
        gh.post_review(123, pr["head"]["sha"], body, comments)
    """

    def __init__(
        self,
        token: str,
        owner: str,
        repo: str,
        base_url: str = "https://api.github.com",
    ):
        self._token = token
        self.owner = owner
        self.repo = repo
        self.base_url = base_url.rstrip("/")
        # Bearer auth; pinned Accept + API version for stable response shapes.
        # The token lives only inside this header dict and is never logged.
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": _API_VERSION,
        }
        self._client = httpx.Client(timeout=30.0, headers=self._headers)

    # -- lifecycle -----------------------------------------------------------

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._client.close()

    def __enter__(self) -> "GitHubClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- internal helpers ----------------------------------------------------

    def _url(self, path: str) -> str:
        """Build a full repo-scoped URL from a ``/...`` path fragment."""
        return f"{self.base_url}{path}"

    def _repo_path(self, suffix: str) -> str:
        """``/repos/{owner}/{repo}{suffix}`` URL for the configured repo."""
        return self._url(f"/repos/{self.owner}/{self.repo}{suffix}")

    # -- reads ---------------------------------------------------------------

    def get_pr(self, pr: int) -> dict:
        """Fetch full PR metadata (title, body, head sha, base/head refs, ...)."""
        resp = self._client.get(self._repo_path(f"/pulls/{pr}"))
        resp.raise_for_status()
        return resp.json()

    def get_pr_files(self, pr: int) -> list:
        """Return the PR's changed files (paginated), reviewable ones only.

        Pages ``per_page=100`` until the listing is exhausted or the GitHub
        3000-file cap is reached. Files with no ``patch`` (binary blobs, files
        whose diff GitHub omitted because it was too large, or pure renames with
        no content change) are skipped — there is nothing to anchor a review
        comment to without a patch. Returns the raw GitHub file objects so the
        diff renderer can read ``filename`` / ``status`` / ``patch`` directly.
        """
        files: list = []
        page = 1
        while True:
            resp = self._client.get(
                self._repo_path(f"/pulls/{pr}/files"),
                params={"per_page": _PER_PAGE, "page": page},
            )
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break

            for f in batch:
                # Skip files GitHub could not / did not produce a patch for.
                # Binary files and oversized diffs come back with no "patch".
                if not f.get("patch"):
                    continue
                files.append(f)

            # Stop on the last (short) page or once we hit the file cap.
            if len(batch) < _PER_PAGE or len(files) >= _MAX_PR_FILES:
                break
            page += 1

        return files

    def get_permission(self, username: str) -> str:
        """Return the collaborator permission level for ``username``.

        Calls ``GET /collaborators/{username}/permission`` and returns the
        ``permission`` field — one of ``admin`` / ``write`` / ``maintain`` /
        ``triage`` / ``read`` / ``none``. On any error (404 = not a
        collaborator, 403 = token lacks scope) it degrades to ``"none"`` so the
        authz layer fails closed rather than crashing the run.
        """
        try:
            resp = self._client.get(
                self._repo_path(f"/collaborators/{username}/permission")
            )
            resp.raise_for_status()
        except httpx.HTTPError:
            return "none"
        data = resp.json()
        perm = data.get("permission")
        return perm if isinstance(perm, str) and perm else "none"

    # -- writes (comments only — never touches the repo tree) ----------------

    def post_review(
        self,
        pr: int,
        commit_id: str,
        body: str,
        comments: list,
    ) -> dict:
        """Post ONE batched review with all inline comments in a single call.

        Uses ``POST /pulls/{n}/reviews`` with ``event=COMMENT`` so the whole
        review lands atomically as a single non-blocking review (no approve /
        request-changes). ``comments`` is a list of inline-comment dicts; each
        is normalized to ``{path, line, side, body}`` (``side`` defaults to
        ``RIGHT`` — the new side of the diff, which is where reference-line
        anchors point).

        NOTE: the reviews endpoint is ATOMIC — if any single inline comment
        anchors to a line GitHub considers outside the diff, the entire request
        is rejected with 422 and NOTHING is posted. Callers MUST pre-validate
        every anchor against ``valid_anchors`` before calling this. The summary
        ``body`` is posted with no inline comments when ``comments`` is empty.
        """
        payload = {
            "commit_id": commit_id,
            "body": body,
            "event": "COMMENT",
            "comments": [self._normalize_comment(c) for c in (comments or [])],
        }
        resp = self._client.post(
            self._repo_path(f"/pulls/{pr}/reviews"),
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _normalize_comment(c: dict) -> dict:
        """Coerce a finding-shaped inline comment to the reviews API shape.

        Accepts either contract keys (``path``/``line``/``side``/``body``) or
        the schema-style ``relevant_file``/``end_line``/``comment`` keys so the
        entrypoint can pass findings through with minimal reshaping.
        """
        path = c.get("path") or c.get("relevant_file")
        line = c.get("line")
        if line is None:
            # Anchor on the END of the range so multi-line findings attach to
            # the most specific (last) referenced new-side line.
            line = c.get("end_line", c.get("start_line"))
        body = c.get("body") or c.get("comment") or ""
        side = c.get("side") or "RIGHT"
        # Single-line anchor only. A multi-line range (start_line + line) is
        # rejected with an atomic 422 whenever the two endpoints fall in
        # different diff hunks — and because the reviews endpoint is atomic, one
        # such comment kills the whole batched review. A single new-side line is
        # far more robust and the reference-line anchor is what carries meaning.
        return {"path": path, "line": int(line), "side": side, "body": body}

    def post_inline_comment(self, pr: int, commit_id: str, comment: dict) -> bool:
        """Post ONE standalone inline review comment; return True on success.

        Fallback for when the atomic batched ``post_review`` is rejected (422)
        because a single comment anchors outside the diff: posting each comment
        independently via ``POST /pulls/{n}/comments`` lets the valid ones land
        while only the genuinely-bad anchor is dropped. Never raises — a failed
        comment simply returns False so the caller can keep going.
        """
        c = self._normalize_comment(comment)
        if not c.get("path") or c.get("line") is None:
            return False
        payload = {
            "body": c["body"],
            "commit_id": commit_id,
            "path": c["path"],
            "line": c["line"],
            "side": c["side"],
        }
        try:
            resp = self._client.post(
                self._repo_path(f"/pulls/{pr}/comments"), json=payload
            )
            resp.raise_for_status()
            return True
        except httpx.HTTPError:
            return False

    def upsert_summary_comment(self, pr: int, body: str, marker: str) -> dict:
        """Create or update the single sticky summary comment.

        Scans the PR's issue comments for one whose body contains ``marker`` (a
        hidden HTML-comment sentinel embedded in ``body``). If found, the
        existing comment is edited in place via PATCH; otherwise a new comment
        is created. This keeps exactly one summary comment on the PR across
        re-runs and is how the agent persists ``last_reviewed_sha`` (encoded
        inside ``body``) with no external datastore.
        """
        existing_id = self._find_comment_by_marker(pr, marker)
        if existing_id is not None:
            resp = self._client.patch(
                self._repo_path(f"/issues/comments/{existing_id}"),
                json={"body": body},
            )
            resp.raise_for_status()
            return resp.json()
        return self.post_hint_comment(pr, body)

    def _find_comment_by_marker(self, pr: int, marker: str):
        """Return the id of the first issue comment containing ``marker``.

        Pages issue comments (PR conversation comments live on the issues API)
        until a match is found, the listing is exhausted, or the page cap is
        hit. Returns ``None`` when no marked comment exists yet.
        """
        page = 1
        while page <= _MAX_COMMENT_PAGES:
            resp = self._client.get(
                self._repo_path(f"/issues/{pr}/comments"),
                params={"per_page": _PER_PAGE, "page": page},
            )
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            for comment in batch:
                if marker in (comment.get("body") or ""):
                    return comment.get("id")
            if len(batch) < _PER_PAGE:
                break
            page += 1
        return None

    def post_hint_comment(self, pr: int, body: str) -> dict:
        """Post a plain top-level issue comment on the PR conversation.

        Used both for one-off hints (e.g. unauthorized-author notice, diff too
        large) and as the create path for ``upsert_summary_comment``.
        """
        resp = self._client.post(
            self._repo_path(f"/issues/{pr}/comments"),
            json={"body": body},
        )
        resp.raise_for_status()
        return resp.json()
