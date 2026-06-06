"""
Security Agent
Analyzes PR diffs for security vulnerabilities, secrets exposure,
injection flaws, authentication issues, and OWASP Top 10 concerns.
"""
import structlog

from app.agents.base_agent import AgentResult, BaseAgent
from app.config import settings
from app.services.github_service import PRData

log = structlog.get_logger()

SYSTEM_PROMPT = """You are an elite Application Security Engineer specializing in code review.
Your task is to analyze a Pull Request diff for security vulnerabilities.

Focus on:
1. **Secrets & Credentials** — hardcoded API keys, passwords, tokens, private keys
2. **Injection Flaws** — SQL injection, command injection, LDAP injection, XSS
3. **Authentication & Authorization** — broken auth, missing access checks, insecure JWTs
4. **Cryptography** — weak algorithms (MD5, SHA1), hardcoded salts, insecure randomness
5. **Input Validation** — missing sanitization, path traversal, unsafe deserialization
6. **Dependency Issues** — use of known-vulnerable packages
7. **OWASP Top 10** — any of the top 10 web security risks

You MUST respond with ONLY a valid JSON object — no markdown, no explanation outside JSON.

JSON schema:
{
  "summary": "Brief overall security assessment (1–2 sentences)",
  "risk_level": "critical|high|medium|low|none",
  "findings": [
    {
      "severity": "critical|high|medium|low|info",
      "category": "secrets|injection|auth|crypto|validation|dependency|other",
      "title": "Short issue title",
      "description": "Detailed description of the vulnerability",
      "file": "filename or null",
      "line_hint": "approximate line or code snippet",
      "suggestion": "How to fix this issue"
    }
  ],
  "positive_observations": ["Any good security practices noticed"],
  "tokens_note": null
}

If no security issues found, return an empty findings array with risk_level "none".
"""


class SecurityAgent(BaseAgent):
    name = "security"

    def _get_models(self) -> list[str]:
        return settings.get_model_list(settings.security_models)

    def _build_user_prompt(self, pr_data: PRData) -> str:
        files_summary = "\n".join(
            f"- {f.filename} ({f.language or 'unknown'}) +{f.additions}/-{f.deletions}"
            for f in pr_data.files
        )
        return f"""## Pull Request: {pr_data.title}
**Repository:** {pr_data.repo}
**Author:** {pr_data.author}
**Branch:** {pr_data.head_branch} → {pr_data.base_branch}

## Changed Files ({len(pr_data.files)} files)
{files_summary}

## Full Diff
```diff
{pr_data.full_diff[:30000]}
```

Analyze this diff for security vulnerabilities. Return ONLY the JSON object."""

    async def run(self, pr_data: PRData) -> AgentResult:
        return await self._run_agent(pr_data)
