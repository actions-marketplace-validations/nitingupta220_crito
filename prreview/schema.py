"""Findings schema + single-finding normalizer for the PR review agent.

Pure stdlib. This module is imported by ``ensemble`` and ``postprocess`` and must
stay free of third-party imports (it participates in the stdlib-only smoke test).

``FINDINGS_SCHEMA`` is the exact OpenRouter ``response_format`` json_schema payload
sent as a best-effort structured-output hint. ``normalize_finding`` coerces one
raw model-authored finding dict into the canonical schema shape, returning None
when the finding is too malformed to salvage.

Findings are plain dicts — there is intentionally no shared dataclass.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# The structured-output schema (OpenRouter response_format payload).
# ---------------------------------------------------------------------------

FINDINGS_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "review_findings",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["findings"],
            "properties": {
                "findings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "relevant_file",
                            "start_line",
                            "end_line",
                            "severity",
                            "category",
                            "comment",
                        ],
                        "properties": {
                            "relevant_file": {"type": "string"},
                            "start_line": {"type": "integer"},
                            "end_line": {"type": "integer"},
                            "severity": {
                                "type": "string",
                                "enum": ["critical", "major", "minor", "nit"],
                            },
                            "category": {
                                "type": "string",
                                "enum": [
                                    "correctness",
                                    "bug",
                                    "security",
                                    "style",
                                    "design",
                                ],
                            },
                            "comment": {"type": "string"},
                            "existing_code": {"type": "string"},
                            "improved_code": {"type": "string"},
                            "confidence": {"type": "number"},
                            "rule_id": {"type": "string"},
                        },
                    },
                }
            },
        },
    },
}

# ---------------------------------------------------------------------------
# Canonical enums (single source of truth, in severity order: most severe first).
# ---------------------------------------------------------------------------

SEVERITIES = ["critical", "major", "minor", "nit"]
CATEGORIES = ["correctness", "bug", "security", "style", "design"]

_SEVERITIES_SET = set(SEVERITIES)
_CATEGORIES_SET = set(CATEGORIES)

# Lenient coercion maps: models routinely emit out-of-vocabulary severities and
# categories (they were trained on many different rubrics). Rather than drop the
# finding, fold synonyms onto our canonical vocabulary.
_SEVERITY_ALIASES = {
    "blocker": "critical",
    "high": "major",
    "error": "major",
    "warning": "minor",
    "medium": "minor",
    "low": "nit",
    "info": "nit",
    "informational": "nit",
    "trivial": "nit",
    "suggestion": "nit",
    "note": "nit",
}

_CATEGORY_ALIASES = {
    # correctness / bug family
    "logic": "correctness",
    "logic_error": "correctness",
    "null_deref": "bug",
    "off_by_one": "bug",
    "race_condition": "bug",
    "resource_leak": "bug",
    "error_handling": "bug",
    "async": "bug",
    "type": "bug",
    "infinite_loop": "bug",
    "runtime": "bug",
    "functional": "correctness",
    # security family
    "secrets": "security",
    "injection": "security",
    "auth": "security",
    "authz": "security",
    "crypto": "security",
    "validation": "security",
    "dependency": "security",
    "vulnerability": "security",
    "owasp": "security",
    # performance -> design (no dedicated perf category; it's a design concern)
    "performance": "design",
    "n_plus_one": "design",
    "algorithm": "design",
    "memory": "design",
    "computation": "design",
    "database": "design",
    "network": "design",
    "caching": "design",
    "concurrency": "design",
    "frontend": "design",
    # quality family
    "complexity": "design",
    "solid": "design",
    "dry": "design",
    "function_design": "design",
    "smell": "design",
    "testability": "design",
    "architecture": "design",
    "maintainability": "design",
    "refactor": "design",
    # style family
    "naming": "style",
    "magic_values": "style",
    "dead_code": "style",
    "formatting": "style",
    "convention": "style",
    "documentation": "style",
    "docs": "style",
    "missing_docstring": "style",
    "comments": "style",
    "type_hints": "style",
}


def _coerce_str(value) -> str:
    """Best-effort string coercion. None / missing -> empty string."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _coerce_int(value):
    """Best-effort int coercion. Returns None when nothing sensible can be made.

    Tolerates ``"42"``, ``42.0``, and leading/trailing junk like ``"L42"``.
    """
    if isinstance(value, bool):  # bool is an int subclass — reject explicitly.
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return int(s)
        except ValueError:
            pass
        try:
            return int(float(s))
        except ValueError:
            pass
        # Pull the first run of digits (handles "line 42", "L42", "42:7").
        digits = ""
        started = False
        for ch in s:
            if ch.isdigit():
                digits += ch
                started = True
            elif started:
                break
        if digits:
            try:
                return int(digits)
            except ValueError:
                return None
    return None


def _coerce_severity(value):
    """Map a raw severity onto the canonical vocabulary; None if unmappable."""
    s = _coerce_str(value).strip().lower()
    if not s:
        return None
    if s in _SEVERITIES_SET:
        return s
    return _SEVERITY_ALIASES.get(s)


def _coerce_category(value):
    """Map a raw category onto the canonical vocabulary; None if unmappable."""
    s = _coerce_str(value).strip().lower()
    if not s:
        return None
    if s in _CATEGORIES_SET:
        return s
    return _CATEGORY_ALIASES.get(s)


def _coerce_confidence(value):
    """Clamp a confidence into [0.0, 1.0]; None when absent/garbage.

    Tolerates percentages (``85`` -> ``0.85``) and stringified numbers.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f > 1.0:
        # Treat 0..100 as a percentage.
        f = f / 100.0
    if f < 0.0:
        return 0.0
    if f > 1.0:
        return 1.0
    return f


def normalize_finding(d: dict):
    """Coerce/validate one raw finding into the canonical schema shape.

    Returns a fresh plain dict on success, or ``None`` when the finding is
    missing the irreducible minimum (a file path, a usable line number, a
    comment) or carries a severity/category that cannot be mapped.

    Tolerant by design: models emit ``file``/``path``/``filename`` for the path,
    ``line``/``line_hint`` for the line, ``description``/``message`` for the
    comment, and out-of-vocabulary severity/category labels. We accept the common
    aliases rather than discard otherwise-good findings, but we never invent a
    file path or a line number that the model did not supply.
    """
    if not isinstance(d, dict):
        return None

    # --- file path (required) ---
    relevant_file = _coerce_str(
        d.get("relevant_file")
        or d.get("file")
        or d.get("path")
        or d.get("filename")
        or d.get("file_path")
    ).strip()
    if not relevant_file:
        return None

    # --- line range (start required; end defaults to start) ---
    start_line = _coerce_int(
        d.get("start_line")
        if d.get("start_line") is not None
        else d.get("line")
        if d.get("line") is not None
        else d.get("line_hint")
    )
    if start_line is None:
        return None
    end_line = _coerce_int(d.get("end_line"))
    if end_line is None:
        end_line = start_line
    if start_line > end_line:
        start_line, end_line = end_line, start_line

    # --- severity + category (required, mappable) ---
    severity = _coerce_severity(d.get("severity"))
    if severity is None:
        return None
    category = _coerce_category(d.get("category"))
    if category is None:
        return None

    # --- comment (required, non-empty) ---
    comment = _coerce_str(
        d.get("comment") or d.get("description") or d.get("message") or d.get("title")
    ).strip()
    if not comment:
        return None

    out = {
        "relevant_file": relevant_file,
        "start_line": start_line,
        "end_line": end_line,
        "severity": severity,
        "category": category,
        "comment": comment,
    }

    # --- optional fields: only carry them when meaningfully present ---
    existing_code = _coerce_str(d.get("existing_code")).strip()
    if existing_code:
        out["existing_code"] = existing_code

    improved_code = _coerce_str(
        d.get("improved_code") or d.get("suggestion")
    ).strip()
    if improved_code:
        out["improved_code"] = improved_code

    confidence = _coerce_confidence(d.get("confidence"))
    if confidence is not None:
        out["confidence"] = confidence

    rule_id = _coerce_str(d.get("rule_id")).strip()
    if rule_id:
        out["rule_id"] = rule_id

    return out
