"""Finalize model findings before they are posted as a PR review.

Pure stdlib. Imports ``prreview.schema`` for the canonical severity ordering and
the single-finding normalizer; everything else is local.

The single public function, :func:`finalize`, takes the union/dedup'd findings
produced by the ensemble plus the ``valid_anchors`` set built by
``diff.parse_and_render`` and returns a clean, capped, sorted list of findings
that are guaranteed to anchor to real new-side lines. This is the last gate
before posting, so it is deliberately defensive: a hallucinated line number, an
empty comment, or a duplicate finding never reaches the PR.

Findings are plain dicts shaped per FINDINGS_SCHEMA.
"""

from __future__ import annotations

from prreview.schema import SEVERITIES, normalize_finding

# Rank for sorting: critical (most severe) first. Anything unknown sorts last.
_SEVERITY_RANK = {sev: i for i, sev in enumerate(SEVERITIES)}
_UNKNOWN_RANK = len(SEVERITIES)


def _sev_rank(sev) -> int:
    return _SEVERITY_RANK.get(sev, _UNKNOWN_RANK)


def _anchor_intersects(finding: dict, valid_anchors: set) -> bool:
    """True if any line in [start_line, end_line] for this file is a real anchor.

    The diff renderer recorded ``(path, new_line)`` for every new-side line it
    actually showed the model. A finding survives only if at least one line in
    its claimed range was genuinely present — this rejects lines the model
    invented and lines that live on the removed side of the diff.
    """
    path = finding.get("relevant_file")
    if not path:
        return False
    try:
        start = int(finding.get("start_line"))
        end = int(finding.get("end_line"))
    except (TypeError, ValueError):
        return False
    if start > end:
        start, end = end, start
    for line in range(start, end + 1):
        if (path, line) in valid_anchors:
            return True
    return False


def _has_comment(finding: dict) -> bool:
    """True if the finding carries a non-empty, non-whitespace comment."""
    comment = finding.get("comment")
    return isinstance(comment, str) and bool(comment.strip())


def _dedup_key(finding: dict) -> tuple:
    """Defensive dedup key: same file + same span + same category collapses."""
    return (
        finding.get("relevant_file"),
        finding.get("start_line"),
        finding.get("end_line"),
        finding.get("category"),
    )


def finalize(findings: list, valid_anchors: set, max_findings: int = 30) -> list:
    """Validate, dedup, gate, sort and cap a list of raw findings.

    Pipeline:
      1. Normalize each finding to the schema shape (drop the unsalvageable).
      2. Gate: drop findings with an empty/whitespace comment.
      3. Anchor-validate: drop findings whose [start_line, end_line] range does
         not intersect *valid_anchors* for that file. (If *valid_anchors* is
         empty we skip this step rather than nuke everything — an empty set
         means "no anchor info available", not "nothing is valid".)
      4. Defensive dedup: collapse duplicates on
         (relevant_file, start_line, end_line, category), keeping the
         higher-severity / higher-confidence copy and merging model attribution.
      5. Sort by severity (critical > major > minor > nit), then by descending
         confidence as a stable tiebreak.
      6. Cap to *max_findings*.

    Returns the finalized list of finding dicts.
    """
    if not findings:
        return []

    # 1 + 2 + 3: normalize, gate empties, anchor-validate.
    cleaned: list = []
    have_anchors = bool(valid_anchors)
    for raw in findings:
        if not isinstance(raw, dict):
            continue
        norm = normalize_finding(raw)
        if norm is None:
            continue
        if not _has_comment(norm):
            continue
        if have_anchors and not _anchor_intersects(norm, valid_anchors):
            continue
        cleaned.append(norm)

    # 4: defensive dedup, keeping the strongest copy and merging attribution.
    best: dict = {}
    order: list = []
    for finding in cleaned:
        key = _dedup_key(finding)
        existing = best.get(key)
        if existing is None:
            best[key] = finding
            order.append(key)
            continue
        # Decide which copy to keep: lower severity rank == more severe.
        keep_new = _sev_rank(finding.get("severity")) < _sev_rank(
            existing.get("severity")
        )
        if not keep_new and _sev_rank(finding.get("severity")) == _sev_rank(
            existing.get("severity")
        ):
            # Tie on severity -> prefer higher confidence.
            keep_new = _confidence(finding) > _confidence(existing)

        winner, loser = (finding, existing) if keep_new else (existing, finding)
        _merge_attribution(winner, loser)
        best[key] = winner

    deduped = [best[k] for k in order]

    # 5: sort by severity (critical first), then by descending confidence.
    deduped.sort(
        key=lambda f: (_sev_rank(f.get("severity")), -_confidence(f))
    )

    # 6: cap.
    if max_findings is not None and max_findings >= 0:
        deduped = deduped[:max_findings]

    return deduped


def _confidence(finding: dict) -> float:
    """Best-effort numeric confidence; defaults to 0.0 when absent/garbage."""
    val = finding.get("confidence")
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _merge_attribution(winner: dict, loser: dict) -> None:
    """Fold the loser's model attribution into the winner (union, in place).

    The ensemble may stamp findings with which model(s) produced them under a
    ``models`` (or singular ``model``) key. When two copies collapse during
    dedup we keep the strongest copy's body but record every model that
    independently reported the issue — agreement across models is signal.
    """
    merged: list = []
    seen: set = set()
    for src in (winner, loser):
        models = src.get("models")
        if models is None and src.get("model") is not None:
            models = [src.get("model")]
        if isinstance(models, str):
            models = [models]
        if not models:
            continue
        for m in models:
            if m and m not in seen:
                seen.add(m)
                merged.append(m)
    if merged:
        winner["models"] = merged
