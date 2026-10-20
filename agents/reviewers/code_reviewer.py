"""Code reviewer specialist agent."""

from __future__ import annotations

import json

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from agents.specialists.structured import run_structured_review
from schemas.models import AgentRole, Finding, ReviewCategory


def run_code_review(diff: str, model: str | None = None, max_findings: int = 20) -> list[Finding]:
    """Structured code review used by the graph node."""
    return run_structured_review(AgentRole.CODE_REVIEWER, diff, model=model, max_findings=max_findings)


class AnalyzeDiffInput(BaseModel):
    diff: str = Field(description="Unified diff (or plain source) to analyze")
    focus: ReviewCategory = Field(default=ReviewCategory.CORRECTNESS, description="Review category to focus on")


def _focus_role(focus: ReviewCategory) -> AgentRole:
    from graphs.review_graph import FOCUS_OWNERS  # local import: graphs depends on this module

    return FOCUS_OWNERS[focus]


@tool(args_schema=AnalyzeDiffInput)
def analyze_diff(diff: str, focus: ReviewCategory = ReviewCategory.CORRECTNESS) -> str:
    """Analyze a unified diff for issues of the given focus area; returns findings as JSON."""
    findings = run_structured_review(_focus_role(ReviewCategory(focus)), diff)
    return json.dumps({"focus": ReviewCategory(focus).value, "findings": [f.model_dump(mode="json") for f in findings]})
