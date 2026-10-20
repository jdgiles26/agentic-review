"""Code reviewer specialist agent."""

from __future__ import annotations

from agents.specialists.structured import run_structured_review
from schemas.models import AgentRole, Finding


def run_code_review(diff: str, model: str | None = None, max_findings: int = 20) -> list[Finding]:
    """Structured code review used by the graph node."""
    return run_structured_review(AgentRole.CODE_REVIEWER, diff, model=model, max_findings=max_findings)
