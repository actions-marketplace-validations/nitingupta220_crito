"""Configuration loading for the PR review agent.

Module import is stdlib-only — pyyaml is imported *lazily inside*
:func:`load_config` so simply importing this module (or running the STDLIB-ONLY
smoke test) never requires the third-party ``yaml`` package. Zero-config is a
first-class case: with no ``.crito.yaml`` and no environment overrides the
returned :class:`Config` already carries strong, live-verified defaults.

Precedence (lowest to highest):
  1. Dataclass defaults (zero-config baseline).
  2. ``.crito.yaml`` at the repo root (if present and parseable).
  3. ``OPENROUTER_MODELS`` environment variable (comma-separated) — overrides
     the model list only.

Custom rules are loaded from ``.crito/rules.md`` when present. These are
*trusted* repo-authored instructions and are kept separate from the model list;
the prompt builder places them OUTSIDE the untrusted-diff fence.

The model list is always capped to 3 (OpenRouter's hard limit on ``models[]``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# Live-verified free defaults (2026-06-02 spike):
#   * gpt-oss-120b  — the only reliably JSON-clean free model; leads the chain.
# Verified serving 2026-06-13 against a live free key. The :free roster churns
# (z-ai/glm-4.5-air:free now 404s "use the paid slug"; qwen/qwen3-coder:free is
# upstream-saturated/429), so these are chosen for liveness + lineage diversity
# (OpenAI / NVIDIA / Google => different blind spots => better union recall).
# Always overridable via the OPENROUTER_MODELS env var or .crito.yaml.
DEFAULT_MODELS = [
    "openai/gpt-oss-120b:free",                 # reliable pure-JSON lead
    "nvidia/nemotron-3-super-120b-a12b:free",   # 1M ctx, serving
    "google/gemma-4-31b-it:free",               # 262k ctx, serving
]


@dataclass
class Config:
    """Resolved configuration for a single review run.

    Fields match the public CONTRACT exactly. ``ignore`` defaults to an empty
    tuple via ``default_factory`` so no two Config instances share a mutable
    default. ``custom_rules`` is the raw text of ``.crito/rules.md`` (or
    None when absent).
    """

    models: list
    profile: str = "chill"
    privacy_mode: str = "zdr"
    ignore: list = field(default_factory=list)
    max_diff_chars: int = 60000
    max_files: int = 60
    max_findings: int = 30
    custom_rules: "str | None" = None


def _cap_models(models) -> list:
    """Normalize to a list of non-empty stripped model ids, capped at 3."""
    if isinstance(models, str):
        models = models.split(",")
    cleaned = [str(m).strip() for m in (models or []) if str(m).strip()]
    return cleaned[:3]


def _read_yaml(repo_root: str) -> dict:
    """Read and parse ``.crito.yaml`` from ``repo_root``.

    yaml is imported here (lazily) so module import stays stdlib-only. Any read
    or parse error — including pyyaml not being installed — degrades gracefully
    to an empty dict so zero-config / missing-dep environments still work.
    """
    path = os.path.join(repo_root, ".crito.yaml")
    if not os.path.isfile(path):
        return {}
    try:
        import yaml  # lazy: only needed when a config file actually exists
    except ImportError:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _read_custom_rules(repo_root: str):
    """Return the text of ``.crito/rules.md`` if present, else None."""
    path = os.path.join(repo_root, ".crito", "rules.md")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return None
    text = text.strip()
    return text or None


def load_config(repo_root: str) -> Config:
    """Build a :class:`Config` for ``repo_root``.

    Reads the optional ``.crito.yaml`` and optional ``.crito/rules.md``,
    applies the ``OPENROUTER_MODELS`` env override, caps the model list to 3,
    and falls back to strong defaults so a repo with no config still gets a
    sensible review.
    """
    raw = _read_yaml(repo_root)

    # ── models: defaults <- yaml <- env override ────────────────────────────
    models = _cap_models(raw.get("models")) or list(DEFAULT_MODELS)
    env_models = os.environ.get("OPENROUTER_MODELS")
    if env_models and env_models.strip():
        override = _cap_models(env_models)
        if override:
            models = override

    # ── scalar / list fields: yaml overrides defaults, defaults otherwise ───
    def _get(key, default):
        val = raw.get(key)
        return val if val is not None else default

    ignore_raw = raw.get("ignore")
    if isinstance(ignore_raw, str):
        ignore = [p.strip() for p in ignore_raw.split(",") if p.strip()]
    elif isinstance(ignore_raw, (list, tuple)):
        ignore = [str(p).strip() for p in ignore_raw if str(p).strip()]
    else:
        ignore = []

    cfg = Config(
        models=models,
        profile=str(_get("profile", "chill")),
        privacy_mode=str(_get("privacy_mode", "zdr")),
        ignore=ignore,
        max_diff_chars=int(_get("max_diff_chars", 60000)),
        max_files=int(_get("max_files", 60)),
        max_findings=int(_get("max_findings", 30)),
    )

    cfg.custom_rules = _read_custom_rules(repo_root)
    return cfg
