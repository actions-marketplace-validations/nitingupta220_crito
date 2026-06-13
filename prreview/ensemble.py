"""Ensemble review: fan the SAME prompt out to <=3 different models, union+dedup.

Imports ``prreview.schema`` for normalization; otherwise pure stdlib (asyncio).
Deliberately does NOT import ``httpx`` — the ``client`` is injected, so this
module stays in the stdlib-only smoke test's reach.

This is the lean replacement for the old 5-specialist + aggregator swarm. Instead
of five different prompts, there is ONE shared (system, user) prompt. We send it
to each model in ``models`` (already capped at <=3 by the client) concurrently,
normalize every returned finding to the schema shape, stamp it with the model
that produced it, then collapse near-duplicates across models. Agreement between
independent models is signal, so when two models report overlapping issues we
keep the strongest copy and remember every model that flagged it.
"""

from __future__ import annotations

import asyncio

from prreview.schema import SEVERITIES, normalize_finding

# Severity ordering: index 0 ("critical") is most severe. Unknown sorts last.
_SEVERITY_RANK = {sev: i for i, sev in enumerate(SEVERITIES)}
_UNKNOWN_RANK = len(SEVERITIES)


def _sev_rank(sev) -> int:
    return _SEVERITY_RANK.get(sev, _UNKNOWN_RANK)


def _confidence(finding: dict) -> float:
    """Best-effort numeric confidence; 0.0 when absent/garbage."""
    val = finding.get("confidence")
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


async def _one_model(client, model: str, system: str, user: str, response_format):
    """Run the shared prompt against a single model; return its findings list.

    Sends ONLY this one model so the served result is attributable to it (the
    client's models[] array is used for provider-side fallback within a call,
    but for the ensemble we want one explicit model per call). Tolerant of every
    failure mode: a model that errors, returns None, or returns a non-dict simply
    contributes zero findings rather than failing the whole review.
    """
    try:
        # Temporarily pin the client to this single model so attribution and the
        # served result line up. The client truncates/copies internally.
        original_models = client.models
        try:
            client.models = [model]
            parsed, served_model = await client.chat_json(
                system=system,
                user=user,
                response_format=response_format,
            )
        finally:
            client.models = original_models
    except Exception:
        # Network error, HTTP error after retries, etc. This model abstains.
        return []

    if not isinstance(parsed, dict):
        return []

    raw_findings = parsed.get("findings")
    if not isinstance(raw_findings, list):
        return []

    attributed = served_model or model
    out = []
    for raw in raw_findings:
        if not isinstance(raw, dict):
            continue
        norm = normalize_finding(raw)
        if norm is None:
            continue
        # Stamp attribution so dedup can record cross-model agreement and the
        # telemetry/postprocess layers can report which models flagged what.
        norm["models"] = [attributed]
        out.append(norm)
    return out


async def run_ensemble(client, models: list, system: str, user: str, response_format=None):
    """Send the SAME (system, user) to each model and return dedup(union).

    - ``models`` is the <=3 model fallback array. Each model gets the identical
      shared prompt (no per-model specialization — the specialist checklists live
      inside the single system prompt).
    - Calls run concurrently; a model that fails contributes nothing.
    - All returned findings are normalized, unioned, then ``dedup``'d so that the
      same issue reported by multiple models collapses to one finding carrying
      every reporting model's attribution.

    Returns a list of finding dicts (schema-shaped). Anchor validation, gating,
    and capping happen later in ``postprocess.finalize``.
    """
    # Defensive: cap to 3 and drop falsy entries even if the caller didn't.
    selected = [m for m in (models or []) if m][:3]
    if not selected:
        return []

    results = await asyncio.gather(
        *(_one_model(client, m, system, user, response_format) for m in selected)
    )

    union: list = []
    for findings in results:
        union.extend(findings)

    return dedup(union)


def _overlaps(a: dict, b: dict) -> bool:
    """True if two findings refer to the same file with overlapping line ranges.

    Inclusive overlap test on ``[start_line, end_line]``. Two findings that touch
    the same region of the same file are candidates to be merged when they also
    share a category.
    """
    if a.get("relevant_file") != b.get("relevant_file"):
        return False
    try:
        a_start, a_end = int(a["start_line"]), int(a["end_line"])
        b_start, b_end = int(b["start_line"]), int(b["end_line"])
    except (KeyError, TypeError, ValueError):
        return False
    if a_start > a_end:
        a_start, a_end = a_end, a_start
    if b_start > b_end:
        b_start, b_end = b_end, b_start
    return a_start <= b_end and b_start <= a_end


def _merge_models(winner: dict, loser: dict) -> None:
    """Union the loser's model attribution into the winner, in place."""
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


def dedup(findings: list) -> list:
    """Collapse duplicate findings across models.

    Two findings are duplicates when they share the same file, the same category,
    AND have overlapping line ranges. From a duplicate group we keep the single
    strongest representative — highest severity, then highest confidence — and
    fold every duplicate's model attribution into it so the kept finding records
    every model that independently reported the issue.

    Findings that overlap but disagree on category are kept separate (a security
    issue and a style issue on the same line are two distinct findings).

    Preserves first-seen order of the kept representatives so output is stable.
    """
    if not findings:
        return []

    kept: list = []  # representative finding dicts, in first-seen order
    for finding in findings:
        if not isinstance(finding, dict):
            continue

        matched_index = None
        for i, rep in enumerate(kept):
            if rep.get("category") == finding.get("category") and _overlaps(
                rep, finding
            ):
                matched_index = i
                break

        if matched_index is None:
            kept.append(finding)
            continue

        rep = kept[matched_index]
        # Pick the stronger copy: lower severity rank == more severe.
        new_rank = _sev_rank(finding.get("severity"))
        rep_rank = _sev_rank(rep.get("severity"))
        if new_rank < rep_rank:
            keep_new = True
        elif new_rank > rep_rank:
            keep_new = False
        else:
            keep_new = _confidence(finding) > _confidence(rep)

        if keep_new:
            _merge_models(finding, rep)
            kept[matched_index] = finding
        else:
            _merge_models(rep, finding)

    return kept
