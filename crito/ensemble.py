"""Ensemble review: fan the SAME prompt out to <=3 different models, union+dedup.

Imports ``crito.schema`` for normalization; otherwise pure stdlib (asyncio).
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

from crito.errors import KeyFatal
from crito.schema import SEVERITIES, normalize_finding

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


async def _claim_next(state):
    """Hand out the next model from the shared ranked iterator (lock-guarded).

    Every model in the ranked pool is given to AT MOST ONE slot, in rank order;
    returns ``None`` once the pool is exhausted. The lock scopes only the cheap
    ``next()`` — never an ``await`` on the network — so the ensemble stays
    concurrent. This is what guarantees no two slots land on the same model and
    that the total number of model calls is bounded by the pool size.
    """
    async with state["lock"]:
        return next(state["it"], None)


def _findings_from(parsed, served, requested):
    """Normalize one model's parsed response into stamped finding dicts."""
    raw_findings = parsed.get("findings")
    if not isinstance(raw_findings, list):
        return None  # served but malformed -> let the slot advance
    attributed = served or requested
    out = []
    for raw in raw_findings:
        if not isinstance(raw, dict):
            continue
        norm = normalize_finding(raw)
        if norm is None:
            continue
        # Stamp attribution so dedup can record cross-model agreement and the
        # telemetry layer can report which models actually served.
        norm["models"] = [attributed]
        out.append(norm)
    return out


async def _slot_worker(client, state, system: str, user: str, response_format):
    """One ensemble slot.

    Claims a model from the shared ranked pool and runs the shared prompt against
    it. If that model is UNAVAILABLE — HTTP failure after retries (429/404/503/…),
    a network error, or empty/unparseable content — the slot ADVANCES to the next
    model in the ranked pool and tries again, until a model serves a usable result
    or the pool is exhausted (then the slot abstains, returning ``[]``).

    A served model that genuinely finds nothing returns ``[]`` and the slot stops
    (a clean verdict is a success, not a miss). ``KeyFatal`` (dead key) is NOT a
    per-model problem, so it propagates and aborts the whole run.
    """
    while True:
        model = await _claim_next(state)
        if model is None:
            return []  # ranked pool exhausted -> this slot abstains
        try:
            parsed, served = await client.chat_json(
                system=system,
                user=user,
                response_format=response_format,
                models=[model],
            )
        except KeyFatal:
            raise  # key/billing dead — advancing models cannot help
        except Exception:
            continue  # ModelUnavailable / network / etc. -> next ranked model

        if not isinstance(parsed, dict):
            continue  # empty content / unparseable JSON -> next ranked model

        findings = _findings_from(parsed, served, model)
        if findings is None:
            continue  # served but malformed -> next ranked model
        return findings  # served (0+ findings) -> slot done


async def run_ensemble(client, pool: list, system: str, user: str,
                       response_format=None, active_slots: int = 3):
    """Send the SAME prompt to ``active_slots`` distinct models from the TOP of the
    ranked ``pool``; each slot fails over down the pool and returns dedup(union).

    - ``pool`` is the FULL ranked failover list (best -> worst for code review).
      The first ``active_slots`` still-serving models become the ensemble; if one
      is saturated/dead, that slot advances down the pool to the next available
      model — so a slot only contributes nothing when the whole pool is exhausted.
    - Each model is tried by at most one slot (shared lock-guarded iterator), so
      no two slots use the same model and total model calls <= len(pool).
    - All returned findings are normalized, unioned, then ``dedup``'d so the same
      issue reported by multiple models collapses to one finding carrying every
      reporting model's attribution.

    (``pool`` was previously named ``models``; same first positional argument.)
    """
    seen: set = set()
    selected: list = []
    for m in (pool or []):
        if m and m not in seen:
            seen.add(m)
            selected.append(m)
    if not selected:
        return []

    n = min(max(1, active_slots), len(selected))
    state = {"it": iter(selected), "lock": asyncio.Lock()}

    results = await asyncio.gather(
        *(_slot_worker(client, state, system, user, response_format)
          for _ in range(n))
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
