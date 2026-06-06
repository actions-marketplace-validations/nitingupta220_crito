"""
Static Analysis Service
Runs local static analysis tools (Pylint, Bandit) on Python files
from the PR diff without needing to clone the full repo.
Parses tool output into a unified findings format.
"""
import json
import subprocess
import tempfile
import os
from dataclasses import dataclass, field
import structlog

from app.services.github_service import PRFile

log = structlog.get_logger()


@dataclass
class StaticFinding:
    tool: str
    severity: str        # critical | high | medium | low | info
    category: str
    message: str
    filename: str
    line: int | None = None
    col: int | None = None
    rule_id: str | None = None


@dataclass
class StaticAnalysisResult:
    findings: list[StaticFinding] = field(default_factory=list)
    tools_run: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "tools_run": self.tools_run,
            "errors": self.errors,
            "total_findings": len(self.findings),
            "findings": [
                {
                    "tool": f.tool,
                    "severity": f.severity,
                    "category": f.category,
                    "message": f.message,
                    "filename": f.filename,
                    "line": f.line,
                    "rule_id": f.rule_id,
                }
                for f in self.findings
            ],
        }


def _bandit_severity(bandit_sev: str) -> str:
    """Map Bandit severity to our scale."""
    return {
        "HIGH": "high",
        "MEDIUM": "medium",
        "LOW": "low",
    }.get(bandit_sev.upper(), "info")


def _pylint_severity(pylint_type: str) -> str:
    """Map Pylint message type to our severity scale."""
    return {
        "E": "high",    # Error
        "F": "critical", # Fatal
        "W": "medium",  # Warning
        "C": "low",     # Convention
        "R": "info",    # Refactor
        "I": "info",    # Info
    }.get(pylint_type[:1].upper(), "info")


class StaticAnalysisService:
    """
    Runs static analysis on patch content extracted from PR files.
    Creates temporary files to run tools against — no git clone needed.
    """

    def analyze_pr_files(self, files: list[PRFile]) -> StaticAnalysisResult:
        """Run static analysis on all relevant PR files."""
        result = StaticAnalysisResult()

        python_files = [f for f in files if f.language == "python" and f.patch]

        if python_files:
            self._run_bandit(python_files, result)
            self._run_pylint(python_files, result)

        if not python_files:
            log.info("No Python files with patches to analyze statically")

        return result

    # ── Bandit (Python security) ─────────────────────────────────────────────

    def _run_bandit(self, files: list[PRFile], result: StaticAnalysisResult) -> None:
        """Run Bandit security linter on Python patch content."""
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                temp_paths = self._write_patch_files(files, tmpdir, ".py")
                if not temp_paths:
                    return

                cmd = [
                    "bandit", "-r", tmpdir,
                    "-f", "json",
                    "--quiet",
                ]
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=60
                )
                # Bandit exits 1 when it finds issues — that's fine
                if proc.stdout:
                    self._parse_bandit_output(proc.stdout, result)
                    result.tools_run.append("bandit")

        except FileNotFoundError:
            log.warning("Bandit not installed — skipping security static analysis")
            result.errors.append("bandit not found in PATH")
        except subprocess.TimeoutExpired:
            log.warning("Bandit timed out")
            result.errors.append("bandit timed out")
        except Exception as exc:
            log.warning("Bandit failed", error=str(exc))
            result.errors.append(f"bandit error: {exc}")

    def _parse_bandit_output(self, stdout: str, result: StaticAnalysisResult) -> None:
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            return

        for issue in data.get("results", []):
            result.findings.append(
                StaticFinding(
                    tool="bandit",
                    severity=_bandit_severity(issue.get("issue_severity", "LOW")),
                    category="security",
                    message=f"[{issue.get('test_id', '')}] {issue.get('issue_text', '')}",
                    filename=os.path.basename(issue.get("filename", "")),
                    line=issue.get("line_number"),
                    rule_id=issue.get("test_id"),
                )
            )

    # ── Pylint (Python quality) ──────────────────────────────────────────────

    def _run_pylint(self, files: list[PRFile], result: StaticAnalysisResult) -> None:
        """Run Pylint on Python patch content."""
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                temp_paths = self._write_patch_files(files, tmpdir, ".py")
                if not temp_paths:
                    return

                cmd = [
                    "pylint",
                    *temp_paths,
                    "--output-format=json",
                    "--disable=C0114,C0115,C0116",  # Skip missing docstring warnings (agent handles)
                    "--score=no",
                ]
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=60
                )
                if proc.stdout:
                    self._parse_pylint_output(proc.stdout, result)
                    result.tools_run.append("pylint")

        except FileNotFoundError:
            log.warning("Pylint not installed — skipping quality static analysis")
            result.errors.append("pylint not found in PATH")
        except subprocess.TimeoutExpired:
            log.warning("Pylint timed out")
            result.errors.append("pylint timed out")
        except Exception as exc:
            log.warning("Pylint failed", error=str(exc))
            result.errors.append(f"pylint error: {exc}")

    def _parse_pylint_output(self, stdout: str, result: StaticAnalysisResult) -> None:
        try:
            messages = json.loads(stdout)
        except json.JSONDecodeError:
            return

        for msg in messages:
            msg_type = msg.get("type", "I")
            if msg_type in ("C", "R", "I"):  # skip style-only issues to reduce noise
                continue
            result.findings.append(
                StaticFinding(
                    tool="pylint",
                    severity=_pylint_severity(msg_type),
                    category="quality",
                    message=f"[{msg.get('message-id', '')}] {msg.get('message', '')}",
                    filename=os.path.basename(msg.get("path", "")),
                    line=msg.get("line"),
                    col=msg.get("column"),
                    rule_id=msg.get("message-id"),
                )
            )

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _write_patch_files(
        self, files: list[PRFile], tmpdir: str, ext: str
    ) -> list[str]:
        """
        Write only the added lines from each file's patch to temp files.
        Returns list of written file paths.
        """
        paths = []
        for pr_file in files:
            if not pr_file.patch:
                continue
            # Extract only added lines from the diff (lines starting with '+' but not '+++')
            added_lines = [
                line[1:]  # strip the leading '+'
                for line in pr_file.patch.splitlines()
                if line.startswith("+") and not line.startswith("+++")
            ]
            if not added_lines:
                continue

            safe_name = pr_file.filename.replace("/", "_").replace("\\", "_")
            temp_path = os.path.join(tmpdir, safe_name)
            with open(temp_path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(added_lines))
            paths.append(temp_path)

        return paths


# Singleton instance
static_analysis_service = StaticAnalysisService()
