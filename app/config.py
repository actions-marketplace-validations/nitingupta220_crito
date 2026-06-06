from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── GitHub ──────────────────────────────────────────────────────────────
    github_token: str = ""          # PAT or GITHUB_TOKEN from Actions
    github_webhook_secret: str = "changeme"

    # ── OpenRouter ──────────────────────────────────────────────────────────
    openrouter_api_key: str
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # ── Model Chain (live-verified free defaults, 2026-06-02 spike) ─────────
    # Lead model MUST be JSON-reliable. gpt-oss-120b is the only reliably-JSON
    # free model as of the spike. qwen3-coder is the strongest free coder.
    # glm-4.5-air is a thinking model — needs large max_tokens.
    #
    # Override per-agent in .env using the AGENT_MODELS_* variables below.
    # Models are processed as "model1,model2,model3" comma-separated strings.
    #
    free_model_chain: str = "openai/gpt-oss-120b:free,qwen/qwen3-coder:free,z-ai/glm-4.5-air:free"

    # Per-agent overrides — leave empty to use free_model_chain
    security_models: str = "openai/gpt-oss-120b:free,qwen/qwen3-coder:free,z-ai/glm-4.5-air:free"
    bug_models: str = "qwen/qwen3-coder:free,openai/gpt-oss-120b:free,z-ai/glm-4.5-air:free"
    performance_models: str = "qwen/qwen3-coder:free,openai/gpt-oss-120b:free,z-ai/glm-4.5-air:free"
    quality_models: str = "openai/gpt-oss-120b:free,qwen/qwen3-coder:free,z-ai/glm-4.5-air:free"
    documentation_models: str = "openai/gpt-oss-120b:free,qwen/qwen3-coder:free,z-ai/glm-4.5-air:free"
    aggregator_models: str = "openai/gpt-oss-120b:free,qwen/qwen3-coder:free,z-ai/glm-4.5-air:free"

    # ── Database ────────────────────────────────────────────────────────────
    database_url: str = "sqlite+aiosqlite:///./pr_review.db"

    # ── Diff Processing ─────────────────────────────────────────────────────
    max_diff_size: int = 50000      # character cap on the full diff sent to LLM
    max_diff_tokens: int = 30000    # approximate token cap per-agent prompt

    # ── Noise filtering — these file patterns are stripped before LLM ───────
    # Comma-separated glob patterns
    ignore_path_patterns: str = (
        "*.lock,package-lock.json,yarn.lock,pnpm-lock.yaml,"
        "*.min.js,*.min.css,dist/*,build/*,.next/*,"
        "*.pb.go,*.generated.*,*_generated.go,*.snap"
    )

    # ── App ─────────────────────────────────────────────────────────────────
    app_env: str = "development"
    log_level: str = "INFO"

    # ── Legacy single-model fields (kept for backward compat, not used) ─────
    default_model: Optional[str] = None
    security_model: Optional[str] = None
    bug_model: Optional[str] = None
    performance_model: Optional[str] = None
    quality_model: Optional[str] = None
    documentation_model: Optional[str] = None
    aggregator_model: Optional[str] = None

    def get_model_list(self, chain_str: str) -> list[str]:
        """Parse a comma-separated model chain string into a list (max 3)."""
        models = [m.strip() for m in chain_str.split(",") if m.strip()]
        return models[:3]  # OpenRouter hard-cap: max 3 in models[]


settings = Settings()
