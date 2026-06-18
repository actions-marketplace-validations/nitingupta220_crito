"""Gitleaks-style secret pre-scan for the PR review agent.

Pure stdlib (``re`` only). This module is deliberately self-contained: it does
NOT import ``crito.schema`` (or anything that pulls in httpx), so it is safe
for the STDLIB-ONLY smoke test and can run as the very first step of the
pipeline — before any text reaches a model.

Why scan first
==============
Free OpenRouter models may route to a training-enabled provider, so any secret
present in a PR diff must be removed *before* prompt assembly and *before* the
diff is clipped to the token budget (a secret living in an oversized region
would otherwise slip through un-redacted). :func:`redact` replaces every match
with the literal token ``[REDACTED_SECRET]`` and, as a bonus, emits one
high-severity ``security`` finding per detection so maintainers are told a
credential leaked into the PR.

Output findings are plain dicts shaped per FINDINGS_SCHEMA
(``severity="critical"``, ``category="security"``). They carry no anchor we can
trust to a specific new-side line on their own, so the orchestrator should run
them through the same anchor-validation as model findings; the
``relevant_file``/``start_line``/``end_line`` we set are best-effort, computed
from the secret's byte offset in the supplied text.
"""

from __future__ import annotations

import re

# ── Replacement token ────────────────────────────────────────────────────────
REDACTION_PLACEHOLDER = "[REDACTED_SECRET]"


# ── Detection rules ──────────────────────────────────────────────────────────
# Each rule is (rule_id, human_label, compiled_pattern). Patterns are ordered
# most-specific first so a structured token (e.g. a GitHub PAT) is matched by
# its dedicated rule rather than the catch-all generic-assignment rule.
#
# The capture group named ``secret`` (when present) marks the exact span to
# replace; if a rule has no ``secret`` group the whole match is replaced. This
# lets the generic ``KEY = "..."`` rule keep the variable name visible while
# blanking only the value.
_RULES: list[tuple[str, str, "re.Pattern[str]"]] = [
    # ── Private key blocks (PEM) ─────────────────────────────────────────────
    # Whole block, multi-line, any key type (RSA / EC / OPENSSH / PGP / plain).
    (
        "private-key",
        "Private key block",
        re.compile(
            r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY-----"
            r"[\s\S]*?"
            r"-----END (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY-----",
        ),
    ),
    # ── AWS access key id ────────────────────────────────────────────────────
    (
        "aws-access-key-id",
        "AWS access key ID",
        re.compile(r"\b(?:AKIA|ASIA|ABIA|ACCA|AGPA|AIDA|AIPA|ANPA|ANVA|AROA|AIPA)[0-9A-Z]{16}\b"),
    ),
    # ── GitHub tokens (PAT / OAuth / app / refresh / fine-grained) ───────────
    (
        "github-token",
        "GitHub token",
        re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[0-9A-Za-z]{36,255}\b"),
    ),
    (
        "github-pat-fine-grained",
        "GitHub fine-grained PAT",
        re.compile(r"\bgithub_pat_[0-9A-Za-z_]{60,255}\b"),
    ),
    # ── OpenAI / OpenRouter / Anthropic style sk- keys ───────────────────────
    # sk-..., sk-proj-..., sk-or-v1-... (OpenRouter), sk-ant-... (Anthropic).
    (
        "openai-api-key",
        "OpenAI/OpenRouter API key",
        re.compile(r"\bsk-(?:proj-|or-v1-|ant-)?[0-9A-Za-z_-]{16,}\b"),
    ),
    # ── Slack tokens ─────────────────────────────────────────────────────────
    (
        "slack-token",
        "Slack token",
        re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b"),
    ),
    # ── Google API key ───────────────────────────────────────────────────────
    (
        "google-api-key",
        "Google API key",
        re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    ),
    # ── JWT (header.payload.signature) ───────────────────────────────────────
    # First segment of a JWT always begins ``eyJ`` (base64 of ``{"``).
    (
        "jwt",
        "JSON Web Token",
        re.compile(r"\beyJ[0-9A-Za-z_-]{10,}\.[0-9A-Za-z_-]{10,}\.[0-9A-Za-z_-]{10,}\b"),
    ),
    # ── Generic high-entropy assignment ──────────────────────────────────────
    # KEY = "value" / SECRET: 'value' / api_token=value where the name looks
    # credential-ish and the value is long enough to be a real secret. Only the
    # value is captured (``secret`` group) so the assignment stays readable.
    (
        "generic-api-key",
        "Hardcoded credential assignment",
        re.compile(
            r"(?i)\b[\w.-]*(?:api[_-]?key|secret|token|passwd|password|access[_-]?key|auth)"
            r"[\w.-]*\s*[:=]\s*"
            r"(?P<q>[\"'])(?P<secret>(?:(?!(?P=q)).){8,})(?P=q)",
        ),
    ),
]


def _line_span_for(text: str, start: int, end: int) -> tuple[int, int]:
    """Return the 1-based (start_line, end_line) covered by ``text[start:end]``.

    Used so each emitted finding can point at roughly where the secret lived.
    Cheap newline counting — fine for the modest sizes a PR diff reaches.
    """
    start_line = text.count("\n", 0, start) + 1
    end_line = text.count("\n", 0, max(start, end - 1)) + 1
    return start_line, end_line


def _make_finding(rule_id: str, label: str, start_line: int, end_line: int) -> dict:
    """Build one schema-shaped, critical-severity security finding.

    The matched secret text itself is NEVER placed in the finding — only the
    fact that *a* secret of a given kind was found and redacted. This keeps the
    credential out of the comment we post back to the PR.
    """
    return {
        "relevant_file": "",  # filled in by caller when it scans a specific file
        "start_line": int(start_line),
        "end_line": int(end_line),
        "severity": "critical",
        "category": "security",
        "comment": (
            f"Hardcoded secret detected ({label}). A credential appears to be "
            f"committed in this change and was redacted before review. Rotate it "
            f"immediately and remove it from the diff/history; load secrets from "
            f"the environment or a secrets manager instead."
        ),
        "confidence": 0.9,
        "rule_id": f"secret:{rule_id}",
    }


def redact(text: str) -> tuple:
    """Redact secrets in ``text`` and emit a finding per detection.

    Replaces every detected secret with :data:`REDACTION_PLACEHOLDER`
    (``[REDACTED_SECRET]``) and returns ``(redacted_text, findings)`` where each
    finding is a plain dict shaped per FINDINGS_SCHEMA with
    ``severity="critical"`` and ``category="security"``.

    Line numbers on the findings are computed against the *original* text so
    they remain meaningful even though redaction shortens the string. Overlapping
    matches from different rules are coalesced: the most-specific rule wins for
    any given span (rules are tried in order, and already-redacted regions are
    not re-scanned).

    This is intentionally conservative about double-reporting: at most one
    finding is emitted per redacted span.
    """
    if not text:
        return text, []

    findings: list[dict] = []

    # Collect non-overlapping match spans across all rules. Earlier rules in
    # ``_RULES`` (more specific) claim their span first; later rules skip any
    # span that overlaps an already-claimed one.
    claimed: list[tuple[int, int]] = []
    matches: list[tuple[int, int, str, str]] = []  # (start, end, rule_id, label)

    def _overlaps(s: int, e: int) -> bool:
        for cs, ce in claimed:
            if s < ce and cs < e:
                return True
        return False

    for rule_id, label, pattern in _RULES:
        for m in pattern.finditer(text):
            if "secret" in (m.groupdict() or {}) and m.group("secret") is not None:
                s, e = m.span("secret")
            else:
                s, e = m.span()
            if e <= s:
                continue
            if _overlaps(s, e):
                continue
            claimed.append((s, e))
            matches.append((s, e, rule_id, label))

    if not matches:
        return text, []

    # Apply redactions right-to-left so earlier offsets stay valid while we
    # splice; emit findings with line numbers from the original text.
    matches.sort(key=lambda t: t[0])
    for s, e, rule_id, label in matches:
        start_line, end_line = _line_span_for(text, s, e)
        findings.append(_make_finding(rule_id, label, start_line, end_line))

    out = text
    for s, e, _rule_id, _label in sorted(matches, key=lambda t: t[0], reverse=True):
        out = out[:s] + REDACTION_PLACEHOLDER + out[e:]

    return out, findings
