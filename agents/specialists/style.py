"""Style specialist."""

from agents.specialists.structured import run_structured_review
from schemas.models import AgentRole, Finding


def run_style_review(diff: str, model: str | None = None) -> list[Finding]:
    return run_structured_review(AgentRole.STYLE_LINTER, diff, model=model)
