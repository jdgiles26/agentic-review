"""Architecture specialist."""

from agents.specialists.structured import run_structured_review
from schemas.models import AgentRole, Finding


def run_architecture_review(diff: str, model: str | None = None, max_findings: int = 20) -> list[Finding]:
    return run_structured_review(AgentRole.ARCHITECTURE_REVIEWER, diff, model=model, max_findings=max_findings)
