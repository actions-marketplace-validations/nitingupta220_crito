"""Diff filtering + rendering for the PR review agent.

Pure stdlib (re, fnmatch). No httpx, no third-party imports — safe for the
STDLIB-ONLY smoke test.

Responsibilities:
  * filter_files   -> drop binary / patch-less / ignored / delete-only files,
                      sorted source-first so the token budget is spent on code.
  * parse_and_render -> turn the kept GitHub "files" objects into a single
                      rendered diff string using ``__new hunk__`` blocks with
                      REFERENCE LINE NUMBERS prepended to every new-side line.
                      This is the line-accuracy trick: the model is shown the
                      exact line number it must anchor a finding to, and we
                      simultaneously build ``valid_anchors`` = the set of
                      (path, new_line) pairs that actually exist in the diff so
                      downstream code can reject hallucinated anchors.

Findings everywhere are plain dicts shaped per FINDINGS_SCHEMA. This module
emits no findings; it only produces the diff text + anchor set.
"""

from __future__ import annotations

import fnmatch
import re

# ── Ignore globs ─────────────────────────────────────────────────────────────
# Lifted/expanded from the salvage source's SKIP_PATTERNS regex, restated as
# fnmatch globs so filename matching is path-aware (matches both the basename
# and any directory segment). Lockfiles, minified/bundled output, vendored and
# generated trees, binary assets, fonts, etc. — never worth model tokens.
SKIP_PATTERNS = [
    # lockfiles / dependency manifests that are machine-generated
    "*.lock",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "Pipfile.lock",
    "Cargo.lock",
    "composer.lock",
    "go.sum",
    # minified / bundled / sourcemaps
    "*.min.js",
    "*.min.css",
    "*.min.*",
    "*.map",
    "*.bundle.js",
    # generated code
    "*.generated.*",
    "*_pb2.py",
    "*_pb2.pyi",
    "*.pb.go",
    "*.g.dart",
    "*.freezed.dart",
    "*.snap",
    "__snapshots__/*",
    # vendored / build / dist trees (match anywhere in the path)
    "vendor/*",
    "*/vendor/*",
    "dist/*",
    "*/dist/*",
    "build/*",
    "*/build/*",
    "node_modules/*",
    "*/node_modules/*",
    "*/generated/*",
    "generated/*",
    "*.egg-info/*",
    # binary / asset / media / font files
    "*.svg",
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.ico",
    "*.webp",
    "*.bmp",
    "*.tiff",
    "*.pdf",
    "*.woff",
    "*.woff2",
    "*.ttf",
    "*.eot",
    "*.otf",
    "*.zip",
    "*.gz",
    "*.tar",
    "*.7z",
    "*.jar",
    "*.so",
    "*.dll",
    "*.dylib",
    "*.class",
    "*.wasm",
    "*.mp4",
    "*.mp3",
    "*.mov",
    "*.parquet",
]

# Source-first ordering (lifted from salvage _SOURCE_EXTS / _CONFIG_EXTS). Source
# code is reviewed first within the budget; docs/config last; everything else in
# the middle.
_SOURCE_EXTS = re.compile(
    r"\.(py|pyi|js|jsx|ts|tsx|mjs|cjs|go|java|rb|rs|cs|cpp|cc|cxx|c|h|hpp|php|"
    r"swift|kt|kts|scala|m|mm|sh|bash|pl|lua|r|dart|ex|exs|clj|sql)$",
    re.IGNORECASE,
)
_CONFIG_EXTS = re.compile(
    r"\.(ya?ml|json|toml|ini|cfg|conf|env|md|rst|txt|xml|properties|gradle|"
    r"dockerfile)$",
    re.IGNORECASE,
)

# A hunk header looks like:  @@ -a,b +c,d @@ optional section heading
_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


# ── helpers ──────────────────────────────────────────────────────────────────


def _is_ignored(filename: str, extra_globs=None) -> bool:
    """True if *filename* matches any default or user ignore glob (basename or path)."""
    if not filename:
        return True
    base = filename.rsplit("/", 1)[-1]
    patterns = SKIP_PATTERNS if not extra_globs else list(SKIP_PATTERNS) + list(extra_globs)
    for pat in patterns:
        if fnmatch.fnmatch(filename, pat) or fnmatch.fnmatch(base, pat):
            return True
    return False


def _file_rank(filename: str) -> int:
    """Sort key: 0 = source, 1 = other, 2 = config/docs (reviewed last)."""
    if _SOURCE_EXTS.search(filename or ""):
        return 0
    if _CONFIG_EXTS.search(filename or ""):
        return 2
    return 1


# ── public: filter_files ─────────────────────────────────────────────────────


def filter_files(files: list, extra_ignore=None) -> tuple:
    """Filter a list of GitHub PR "files" objects.

    *extra_ignore* is an optional list of user globs (from ``.pr-review.yaml``'s
    ``ignore:`` key) that EXTEND the built-in SKIP_PATTERNS.

    Drops, in order:
      * entries with no usable filename,
      * ignored globs (built-in defaults + user ``ignore:`` — lockfiles, minified,
        vendored, binary assets, ...),
      * binary files (GitHub flags them or omits the patch),
      * files with no ``patch`` (binary / too-large / rename-only metadata),
      * pure deletions (``status == "removed"``) — nothing to review on the
        new side, and there are no new-side lines to anchor against.

    Returns ``(kept_files, skipped_filenames)`` where *kept_files* is sorted
    source-first (stable within each rank) and *skipped_filenames* is the list
    of filenames that were dropped (for telemetry).
    """
    kept: list = []
    skipped: list = []

    for f in files or []:
        if not isinstance(f, dict):
            continue
        filename = f.get("filename") or f.get("path") or ""
        if not filename:
            # nothing actionable / unidentifiable
            continue

        status = (f.get("status") or "").lower()

        if _is_ignored(filename, extra_ignore):
            skipped.append(filename)
            continue

        # Pure deletions: no new-side content to review or anchor to.
        if status == "removed":
            skipped.append(filename)
            continue

        # GitHub omits the patch for binary blobs and for files above its size
        # cap; some clients set an explicit binary flag.
        if f.get("binary") is True:
            skipped.append(filename)
            continue

        patch = f.get("patch")
        if not patch or not patch.strip():
            skipped.append(filename)
            continue

        kept.append(f)

    # Stable source-first sort. Python's sort is stable, so equal ranks keep
    # the original (GitHub-provided) order.
    kept.sort(key=lambda f: _file_rank(f.get("filename") or f.get("path") or ""))

    return kept, skipped


# ── public: parse_and_render ─────────────────────────────────────────────────


def _render_one_patch(filename: str, patch: str) -> tuple:
    """Render a single file's unified-diff patch into annotated hunk blocks.

    Returns ``(block_str, anchor_set)`` where *block_str* is the rendered text
    for this file (header + ``__new hunk__`` blocks) and *anchor_set* is the set
    of ``(filename, new_line)`` tuples present on the new side.

    Each new-side line (context or added) is prepended with its REFERENCE LINE
    NUMBER — the actual line number in the post-merge file — so the model can
    cite precise, real line numbers. Only added ("+") lines are marked; context
    lines are shown for orientation but flagged so the system prompt can steer
    the model to comment on "+" lines.
    """
    out_lines: list = [f"## File: {filename}"]
    anchors: set = set()

    new_lineno = 0
    in_hunk = False

    for raw in patch.splitlines():
        header = _HUNK_HEADER_RE.match(raw)
        if header:
            # Start of a new hunk. Begin numbering the new side from +c.
            new_start = int(header.group(3))
            new_lineno = new_start
            in_hunk = True
            section = raw.split("@@", 2)[-1].strip()
            out_lines.append("")
            out_lines.append("__new hunk__")
            if section:
                out_lines.append(f"@@ {section}")
            continue

        if not in_hunk:
            # Lines before the first hunk header (e.g. "\ No newline...") — skip.
            continue

        if not raw:
            # An empty line in a patch represents an empty context line.
            out_lines.append(f"{new_lineno} ")
            anchors.add((filename, new_lineno))
            new_lineno += 1
            continue

        tag = raw[0]
        content = raw[1:]

        if tag == "+":
            # Added line — present on the new side, a valid anchor target.
            out_lines.append(f"{new_lineno} + {content}")
            anchors.add((filename, new_lineno))
            new_lineno += 1
        elif tag == "-":
            # Removed line — NOT on the new side; show it (no number) so the
            # model sees what changed, but it is never an anchor target.
            out_lines.append(f"     - {content}")
        elif tag == "\\":
            # "\ No newline at end of file" — metadata, not a real line.
            continue
        else:
            # Context line (leading space, or git's occasional bare line).
            ctx = content if tag == " " else raw
            out_lines.append(f"{new_lineno}   {ctx}")
            anchors.add((filename, new_lineno))
            new_lineno += 1

    return "\n".join(out_lines), anchors


def parse_and_render(kept_files: list, max_chars: int) -> tuple:
    """Render kept files into one annotated diff string, greedily packed.

    Returns ``(rendered_diff, valid_anchors, skipped_too_large)``:
      * rendered_diff   -- the concatenated ``__new hunk__`` blocks with
                           reference line numbers prepended to new-side lines.
      * valid_anchors   -- set of ``(path, new_line)`` tuples actually present
                           in *rendered_diff*. Downstream code uses this to drop
                           findings that point at lines we never showed.
      * skipped_too_large -- filenames that could not be (fully) included
                           because the char budget was exhausted; oversized
                           blocks are clipped and the file is also listed here.

    Greedy packing: files arrive already source-first (from filter_files), so we
    spend the budget on code first. A block that fits whole is added intact. A
    block that would overflow is clipped to the remaining budget (preserving the
    anchors for the lines that survive the clip) and the file is recorded in
    *skipped_too_large*; once the budget is full we stop.
    """
    if max_chars is None or max_chars <= 0:
        max_chars = 60_000

    rendered_parts: list = []
    valid_anchors: set = set()
    skipped_too_large: list = []
    used = 0

    for f in kept_files or []:
        if not isinstance(f, dict):
            continue
        filename = f.get("filename") or f.get("path") or ""
        patch = f.get("patch") or ""
        if not filename or not patch.strip():
            continue

        block, block_anchors = _render_one_patch(filename, patch)
        # +1 accounts for the newline joiner between blocks.
        sep = 1 if rendered_parts else 0
        block_len = len(block)

        if used + sep + block_len <= max_chars:
            rendered_parts.append(block)
            valid_anchors |= block_anchors
            used += sep + block_len
            continue

        # Block overflows. Try to clip it into whatever budget remains.
        remaining = max_chars - used - sep
        if remaining > 0 and remaining < block_len:
            clipped_lines: list = []
            clip_used = 0
            clip_anchors: set = set()
            # Re-walk the rendered block line-by-line, recomputing which anchors
            # survive so valid_anchors never claims a line we clipped away.
            for line in block.splitlines():
                add_len = len(line) + 1  # newline
                if clip_used + add_len > remaining:
                    break
                clipped_lines.append(line)
                clip_used += add_len
                m = re.match(r"^(\d+) ", line)
                if m:
                    clip_anchors.add((filename, int(m.group(1))))
            if clipped_lines:
                clipped_lines.append(
                    f"... [diff for {filename} clipped — exceeds budget] ..."
                )
                rendered_parts.append("\n".join(clipped_lines))
                valid_anchors |= clip_anchors
                used = max_chars  # budget is now effectively spent
        skipped_too_large.append(filename)
        # Budget exhausted; everything after this file is also too large.
        break

    rendered_diff = "\n".join(rendered_parts)
    return rendered_diff, valid_anchors, skipped_too_large
