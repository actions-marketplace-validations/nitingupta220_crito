"""Sanitize model-authored text before it is posted to GitHub.

Pure stdlib (``re`` only). ``sanitize_comment`` is applied to EVERY piece of
model-generated text (each finding's comment, code snippets, the review summary)
right before it reaches the GitHub API. The model output is, transitively,
influenced by untrusted PR content, so it is treated as untrusted here.

Threats neutralized:
- ``@mention`` / ``@org/team`` notification pings — would spam real users/teams
  every time a review posts. Defanged by inserting a zero-width space after the
  ``@`` so GitHub no longer resolves it, while it still reads as ``@name``.
- Raw HTML — ``<script>``, ``<img>``, ``<details>``, hidden ``<!-- comment -->``
  blocks, etc. GitHub sanitizes most of this, but hidden HTML comments can carry
  invisible directives aimed at downstream LLM tooling, so we strip them.
- Hidden / injected directives like "ignore previous instructions" smuggled into
  a comment body are defanged so they cannot act on any downstream consumer.
- Excessive length — a single comment is capped so a runaway generation cannot
  produce a multi-megabyte review.
"""

from __future__ import annotations

import re

# Maximum length for a single sanitized comment. GitHub hard-limits review/issue
# bodies around 65k characters; we cap well under that per individual comment so
# a batched review stays comfortably within limits.
MAX_COMMENT_LEN = 8000

# A zero-width space. Inserted right after an "@" to break GitHub mention
# resolution while keeping the text visually identical ("@user" stays readable).
_ZERO_WIDTH_SPACE = "​"

# --- @mention defanging ----------------------------------------------------
# A GitHub mention is "@" + login (and optionally "/team"). Logins are
# alphanumeric with single hyphens. We only defang when the "@" begins a word
# (start of string or preceded by whitespace / common punctuation) so we don't
# mangle emails (already handled below) or decorators inside code.
_MENTION_RE = re.compile(
    r"(?P<lead>^|[\s(\[{<>,;:!?\"'])@(?P<handle>[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:/[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)?)"
)

# --- HTML stripping --------------------------------------------------------
# Hidden HTML comments first (they can hide injected directives), then any
# remaining raw tags. Markdown text content is preserved; only the angle-bracket
# markup is removed.
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_HTML_TAG_RE = re.compile(r"</?[A-Za-z][A-Za-z0-9:-]*(?:\s[^<>]*?)?/?>")

# --- hidden directive defanging -------------------------------------------
# Phrases an injection might use to hijack a downstream LLM consumer of this
# text. We don't delete them (that would hide the evidence); we insert a
# zero-width space into the trigger word so it no longer matches, while a human
# can still read what was attempted.
_DIRECTIVE_RE = re.compile(
    r"\b(ignore|disregard|forget|override)\b"
    r"(?=\s+(?:all\s+|any\s+|the\s+|your\s+|previous\s+|prior\s+|above\s+|earlier\s+)*"
    r"(?:instruction|instructions|prompt|prompts|rule|rules|context|message|messages|system))",
    re.IGNORECASE,
)


def _defang_mentions(text: str) -> str:
    """Insert a zero-width space after each leading ``@`` so it can't notify."""
    return _MENTION_RE.sub(
        lambda m: f"{m.group('lead')}@{_ZERO_WIDTH_SPACE}{m.group('handle')}",
        text,
    )


def _strip_html(text: str) -> str:
    """Remove hidden HTML comments and any remaining raw HTML tags."""
    text = _HTML_COMMENT_RE.sub("", text)
    text = _HTML_TAG_RE.sub("", text)
    return text


def _defang_directives(text: str) -> str:
    """Break injected 'ignore previous instructions'-style trigger words."""

    def _break(m: re.Match) -> str:
        word = m.group(1)
        # Split the word with a zero-width space so it reads the same but no
        # longer matches an instruction-hijack pattern downstream.
        return word[0] + _ZERO_WIDTH_SPACE + word[1:]

    return _DIRECTIVE_RE.sub(_break, text)


def _cap_length(text: str) -> str:
    """Truncate to MAX_COMMENT_LEN, appending an explicit truncation marker."""
    if len(text) <= MAX_COMMENT_LEN:
        return text
    marker = "\n\n…[truncated]"
    keep = MAX_COMMENT_LEN - len(marker)
    if keep < 0:
        keep = MAX_COMMENT_LEN
        marker = ""
    return text[:keep].rstrip() + marker


def sanitize_comment(text: str) -> str:
    """Neutralize untrusted model-authored text for safe posting to GitHub.

    Order matters: strip HTML (which removes comment-hidden directives) before
    defanging directive phrases and mentions, then cap length last so the cap
    applies to the final, smaller string.
    """
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    if not text:
        return ""

    text = _strip_html(text)
    text = _defang_mentions(text)
    text = _defang_directives(text)
    text = _cap_length(text)
    return text
