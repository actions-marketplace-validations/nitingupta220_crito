"""
GitHub API Service
Fetches PR metadata, diff, and changed files using the GitHub REST API.
Works in both webhook-server mode and GitHub Actions mode.
"""
import fnmatch
import os
from dataclasses import dataclass, field
from typing import Optional

import httpx
import structlog

from app.config import settings

log = structlog.get_logger()

GITHUB_API_BASE = "https://api.github.com"

# Files matching these globs are stripped before sending to the LLM.
# They add tokens without providing reviewable content.
_NOISE_PATTERNS = [
    "*.lock", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "*.min.js", "*.min.css",
    "dist/*", "build/*", ".next/*", "out/*",
    "*.pb.go", "*.generated.*", "*_generated.go",
    "*.snap", "*.map",
]


def _is_noise_file(filename: str, extra_patterns: list[str] | None = None) -> bool:
    """Return True if the file should be excluded from review."""
    patterns = _NOISE_PATTERNS + (extra_patterns or [])
    return any(fnmatch.fnmatch(filename, p) for p in patterns)


@dataclass
class PRFile:
    filename: str
    status: str          # added, modified, removed, renamed
    additions: int
    deletions: int
    patch: Optional[str]    # the actual diff chunk for this file
    language: Optional[str] = None


@dataclass
class PRData:
    repo: str
    pr_number: int
    pr_id: int
    title: str
    author: str
    body: Optional[str]
    base_branch: str
    head_branch: str
    head_sha: str           # commit SHA of the PR head (needed for inline reviews)
    pr_url: str
    full_diff: str
    files: list[PRFile]
    filtered_files: list[PRFile] = field(default_factory=list)   # noise-stripped
    total_additions: int = 0
    total_deletions: int = 0


def _detect_language(filename: str) -> str | None:
    """Detect language from file extension."""
    ext_map = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".jsx": "javascript",
        ".tsx": "typescript",
        ".java": "java",
        ".go": "go",
        ".rb": "ruby",
        ".php": "php",
        ".cs": "csharp",
        ".cpp": "cpp",
        ".c": "c",
        ".rs": "rust",
        ".sh": "bash",
        ".yml": "yaml",
        ".yaml": "yaml",
        ".json": "json",
        ".md": "markdown",
        ".sql": "sql",
        ".html": "html",
        ".css": "css",
    }
    for ext, lang in ext_map.items():
        if filename.endswith(ext):
            return lang
    return None


class GitHubService:
    def __init__(self):
        # Support both .env token and GITHUB_TOKEN env var (Actions mode)
        token = os.environ.get("GITHUB_TOKEN") or settings.github_token
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def fetch_pr_data(self, repo: str, pr_number: int) -> PRData:
        """Fetch complete PR data: metadata + diff + files."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            # 1. PR metadata
            pr_meta = await self._get_pr_metadata(client, repo, pr_number)

            # 2. Changed files with patches
            files = await self._get_pr_files(client, repo, pr_number)

            # 3. Full unified diff
            full_diff = await self._get_pr_diff(client, repo, pr_number)

        total_additions = sum(f.additions for f in files)
        total_deletions = sum(f.deletions for f in files)

        # Filter noise files for cleaner LLM input
        filtered_files = [f for f in files if not _is_noise_file(f.filename)]
        noise_count = len(files) - len(filtered_files)
        if noise_count:
            log.info("Noise files filtered", count=noise_count)

        log.info(
            "PR data fetched",
            repo=repo,
            pr=pr_number,
            files=len(files),
            filtered_files=len(filtered_files),
            additions=total_additions,
            deletions=total_deletions,
            diff_size=len(full_diff),
        )

        return PRData(
            repo=repo,
            pr_number=pr_number,
            pr_id=pr_meta["id"],
            title=pr_meta["title"],
            author=pr_meta["user"]["login"],
            body=pr_meta.get("body") or "",
            base_branch=pr_meta["base"]["ref"],
            head_branch=pr_meta["head"]["ref"],
            head_sha=pr_meta["head"]["sha"],
            pr_url=pr_meta["html_url"],
            full_diff=full_diff[: settings.max_diff_size],
            files=files,
            filtered_files=filtered_files,
            total_additions=total_additions,
            total_deletions=total_deletions,
        )

    async def post_review_comment(self, repo: str, pr_number: int, body: str) -> int:
        """Post a review comment to the PR. Returns the comment ID."""
        url = f"{GITHUB_API_BASE}/repos/{repo}/issues/{pr_number}/comments"
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, headers=self.headers, json={"body": body})
            response.raise_for_status()
            data = response.json()
            log.info("Posted PR comment", repo=repo, pr=pr_number, comment_id=data["id"])
            return data["id"]

    # ── Private helpers ─────────────────────────────────────────────────────

    async def _get_pr_metadata(self, client: httpx.AsyncClient, repo: str, pr_number: int) -> dict:
        url = f"{GITHUB_API_BASE}/repos/{repo}/pulls/{pr_number}"
        response = await client.get(url, headers=self.headers)
        response.raise_for_status()
        return response.json()

    async def _get_pr_files(self, client: httpx.AsyncClient, repo: str, pr_number: int) -> list[PRFile]:
        url = f"{GITHUB_API_BASE}/repos/{repo}/pulls/{pr_number}/files"
        files = []
        page = 1

        while True:
            response = await client.get(
                url,
                headers=self.headers,
                params={"per_page": 100, "page": page},
            )
            response.raise_for_status()
            data = response.json()
            if not data:
                break

            for f in data:
                files.append(
                    PRFile(
                        filename=f["filename"],
                        status=f["status"],
                        additions=f.get("additions", 0),
                        deletions=f.get("deletions", 0),
                        patch=f.get("patch"),  # may be None for binary/large files
                        language=_detect_language(f["filename"]),
                    )
                )
            if len(data) < 100:
                break
            page += 1

        return files

    async def _get_pr_diff(self, client: httpx.AsyncClient, repo: str, pr_number: int) -> str:
        """Fetch the raw unified diff for the entire PR."""
        url = f"{GITHUB_API_BASE}/repos/{repo}/pulls/{pr_number}"
        headers = {**self.headers, "Accept": "application/vnd.github.diff"}
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.text


# Singleton instance
github_service = GitHubService()
