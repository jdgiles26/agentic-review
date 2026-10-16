"""Shared structured-output runner for specialist agents.

Uses LangChain structured output when OPENAI_API_KEY is set.
Falls back to deterministic heuristics so CI and offline runs still produce findings.
"""

from __future__ import annotations

import os
import re
import uuid
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from agents.base import ROLE_PROMPTS, get_llm
from schemas.models import AgentRole, Finding, ReviewCategory, Severity


class FindingDraft(BaseModel):
    category: ReviewCategory
    severity: Severity
    title: str
    description: str
    file_path: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    suggestion: str | None = None
    confidence: float = 0.8


class FindingBatch(BaseModel):
    """Structured LLM response schema. id is assigned after the model returns."""

    findings: list[FindingDraft] = Field(default_factory=list)
    notes: str = ""


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _to_findings(batch: FindingBatch, role: AgentRole) -> list[Finding]:
    out: list[Finding] = []
    for draft in batch.findings:
        out.append(
            Finding(
                id=_new_id(role.value),
                category=draft.category,
                severity=draft.severity,
                title=draft.title,
                description=draft.description,
                file_path=draft.file_path,
                line_start=draft.line_start,
                line_end=draft.line_end,
                suggestion=draft.suggestion,
                confidence=draft.confidence,
            )
        )
    return out


def heuristic_findings(diff: str, role: AgentRole) -> list[Finding]:
    """Cheap offline signals so reviews are useful without a live model."""
    if not diff.strip():
        return []
    text = diff.lower()
    found: list[Finding] = []

    if role in {AgentRole.CODE_REVIEWER, AgentRole.SECURITY_SPECIALIST}:
        if re.search(r"\b(eval|exec)\s*\(", diff):
            found.append(
                Finding(
                    id=_new_id("sec"),
                    category=ReviewCategory.SECURITY,
                    severity=Severity.CRITICAL,
                    title="Dynamic code execution",
                    description="Diff introduces eval/exec. This is a high-confidence injection risk.",
                    suggestion="Replace with a safe parser or allow-listed dispatcher.",
                    confidence=0.9,
                )
            )
        if re.search(r"(api[_-]?key|secret|password|token)\s*=\s*['\"][^'\"]+['\"]", text):
            found.append(
                Finding(
                    id=_new_id("sec"),
                    category=ReviewCategory.SECURITY,
                    severity=Severity.HIGH,
                    title="Hardcoded secret",
                    description="A credential appears assigned in source.",
                    suggestion="Move the value to an environment variable or secret store.",
                    confidence=0.85,
                )
            )
        if "print(password" in text or "print(token" in text:
            found.append(
                Finding(
                    id=_new_id("sec"),
                    category=ReviewCategory.SECURITY,
                    severity=Severity.HIGH,
                    title="Secret logged",
                    description="Password or token is printed. Logs retain credentials.",
                    suggestion="Remove the print and log a non-sensitive event id instead.",
                    confidence=0.92,
                )
            )

    if role == AgentRole.TEST_SPECIALIST and ("def " in diff or "function " in diff):
        if "test" not in text and "assert" not in text:
            found.append(
                Finding(
                    id=_new_id("test"),
                    category=ReviewCategory.TEST_COVERAGE,
                    severity=Severity.MEDIUM,
                    title="New logic without tests",
                    description="Diff adds behavior but no test or assert appears in the patch.",
                    suggestion="Add a unit test covering the happy path and one failure mode.",
                    confidence=0.6,
                )
            )

    if role == AgentRole.STYLE_LINTER and re.search(r"TODO|FIXME|xxx", diff):
        found.append(
            Finding(
                id=_new_id("style"),
                category=ReviewCategory.STYLE,
                severity=Severity.LOW,
                title="Unresolved TODO",
                description="TODO/FIXME left in the change.",
                suggestion="Track it as an issue or remove it before merge.",
                confidence=0.7,
            )
        )

    if role == AgentRole.ARCHITECTURE_REVIEWER and len(diff) > 8000:
        found.append(
            Finding(
                id=_new_id("arch"),
                category=ReviewCategory.ARCHITECTURE,
                severity=Severity.MEDIUM,
                title="Large cross-cutting change",
                description="Patch is large enough that module boundaries may have shifted.",
                suggestion="Confirm the change stays inside the owning package.",
                confidence=0.5,
            )
        )

    return found


def run_structured_review(
    role: AgentRole,
    diff: str,
    model: str | None = None,
    extra_context: str = "",
    max_findings: int = 20,
) -> list[Finding]:
    """Return structured findings for one specialist."""
    offline = heuristic_findings(diff, role)
    if not os.getenv("OPENAI_API_KEY"):
        return offline[:max_findings]

    llm = get_llm(model)
    prompt = (
        f"{ROLE_PROMPTS[role]}\n\n"
        "Return only structured findings. Do not invent issues. "
        "If nothing material, return an empty findings list.\n"
        f"{extra_context}\n\n"
        f"```diff\n{diff[:12000]}\n```"
    )
    try:
        structured = llm.with_structured_output(FindingBatch)
        batch: FindingBatch | Any = structured.invoke(
            [SystemMessage(content=ROLE_PROMPTS[role]), HumanMessage(content=prompt)]
        )
        if isinstance(batch, FindingBatch):
            llm_findings = _to_findings(batch, role)
        elif isinstance(batch, dict):
            llm_findings = _to_findings(FindingBatch(**batch), role)
        else:
            llm_findings = []
        return (llm_findings or offline)[:max_findings]
    except Exception:
        return offline[:max_findings]
