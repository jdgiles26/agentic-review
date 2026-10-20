"""Shared structured-output runner for specialist agents.

Uses LangChain structured output when OPENAI_API_KEY is set, trying the primary
model then REVIEW_FALLBACK_MODEL. Falls back to deterministic heuristics so CI
and offline runs still produce findings.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from agents.base import ROLE_PROMPTS, UNTRUSTED_CONTENT_NOTE, candidate_models, fence, get_llm, llm_available
from schemas.models import AgentRole, Finding, ReviewCategory, Severity
from tools.diff import AddedLine, changed_files, parse_added_lines
from tools.tracing import record_token_usage, span

log = logging.getLogger(__name__)

MAX_DIFF_CHARS = 12_000
LARGE_DIFF_LINES = 800


class FindingDraft(BaseModel):
    category: ReviewCategory
    severity: Severity
    title: str
    description: str
    file_path: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    suggestion: str | None = None
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)


class FindingBatch(BaseModel):
    """Structured LLM response schema. id is assigned after the model returns."""

    findings: list[FindingDraft] = Field(default_factory=list)
    notes: str = ""


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _to_findings(batch: FindingBatch, role: AgentRole) -> list[Finding]:
    return [Finding(id=_new_id(role.value), **draft.model_dump()) for draft in batch.findings]


def _at(line: AddedLine, **fields: Any) -> Finding:
    return Finding(
        file_path=line.file_path,
        line_start=line.line_no,
        line_end=line.line_no,
        **fields,
    )


_DYNAMIC_EXEC = re.compile(r"\b(eval|exec)\s*\(")
_HARDCODED_SECRET = re.compile(
    r"(api[_-]?key|secret|password|passwd|token)\w*\s*[:=]\s*['\"][^'\"]{3,}['\"]", re.IGNORECASE
)
_LOGGED_SECRET = re.compile(
    r"\b(print|console\.log|log(?:ger)?\.\w+|logging\.\w+)\s*\(.*\b(password|passwd|token|secret)",
    re.IGNORECASE,
)
_TODO = re.compile(r"\b(TODO|FIXME|XXX)\b")
_TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]*$|_test\.\w+$|\.(spec|test)\.\w+$")
_NEW_LOGIC = re.compile(r"^\s*(async\s+def|def|function|class)\s+\w+|=>\s*\{")


def _is_test_path(path: str | None) -> bool:
    return bool(path) and bool(_TEST_PATH.search(path or ""))


def _security_heuristics(added: list[AddedLine]) -> list[Finding]:
    found: list[Finding] = []
    for line in added:
        if _DYNAMIC_EXEC.search(line.text):
            found.append(
                _at(
                    line,
                    id=_new_id("sec"),
                    category=ReviewCategory.SECURITY,
                    severity=Severity.CRITICAL,
                    title="Dynamic code execution",
                    description="Diff introduces eval/exec. This is a high-confidence injection risk.",
                    suggestion="Replace with a safe parser or allow-listed dispatcher.",
                    confidence=0.9,
                )
            )
        if _HARDCODED_SECRET.search(line.text) and not _is_test_path(line.file_path):
            found.append(
                _at(
                    line,
                    id=_new_id("sec"),
                    category=ReviewCategory.SECURITY,
                    severity=Severity.HIGH,
                    title="Hardcoded secret",
                    description="A credential appears assigned in source.",
                    suggestion="Move the value to an environment variable or secret store.",
                    confidence=0.85,
                )
            )
        if _LOGGED_SECRET.search(line.text):
            found.append(
                _at(
                    line,
                    id=_new_id("sec"),
                    category=ReviewCategory.SECURITY,
                    severity=Severity.HIGH,
                    title="Secret logged",
                    description="Password or token is printed. Logs retain credentials.",
                    suggestion="Remove the print and log a non-sensitive event id instead.",
                    confidence=0.92,
                )
            )
    return found


def _test_heuristics(added: list[AddedLine], diff: str) -> list[Finding]:
    files = changed_files(diff) or [line.file_path for line in added if line.file_path]
    if any(_is_test_path(f) for f in files):
        return []
    logic = [line for line in added if _NEW_LOGIC.search(line.text) and not _is_test_path(line.file_path)]
    if not logic or any("assert" in line.text for line in added):
        return []
    return [
        _at(
            logic[0],
            id=_new_id("test"),
            category=ReviewCategory.TEST_COVERAGE,
            severity=Severity.MEDIUM,
            title="New logic without tests",
            description="Diff adds behavior but touches no test file and adds no assertion.",
            suggestion="Add a unit test covering the happy path and one failure mode.",
            confidence=0.6,
        )
    ]


def _style_heuristics(added: list[AddedLine]) -> list[Finding]:
    return [
        _at(
            line,
            id=_new_id("style"),
            category=ReviewCategory.STYLE,
            severity=Severity.LOW,
            title="Unresolved TODO",
            description="TODO/FIXME left in the change.",
            suggestion="Track it as an issue or remove it before merge.",
            confidence=0.7,
        )
        for line in added
        if _TODO.search(line.text)
    ]


def _architecture_heuristics(added: list[AddedLine]) -> list[Finding]:
    if len(added) <= LARGE_DIFF_LINES:
        return []
    return [
        Finding(
            id=_new_id("arch"),
            category=ReviewCategory.ARCHITECTURE,
            severity=Severity.MEDIUM,
            title="Large cross-cutting change",
            description=f"Patch adds {len(added)} lines; module boundaries may have shifted.",
            suggestion="Confirm the change stays inside the owning package, or split it.",
            confidence=0.5,
        )
    ]


def heuristic_findings(diff: str, role: AgentRole) -> list[Finding]:
    """Cheap offline signals over added lines only, so reviews are useful without a model.

    Each heuristic belongs to exactly one specialist so the graph never double-reports.
    """
    if not diff.strip():
        return []
    added = parse_added_lines(diff)
    if role == AgentRole.SECURITY_SPECIALIST:
        return _security_heuristics(added)
    if role == AgentRole.TEST_SPECIALIST:
        return _test_heuristics(added, diff)
    if role == AgentRole.STYLE_LINTER:
        return _style_heuristics(added)
    if role == AgentRole.ARCHITECTURE_REVIEWER:
        return _architecture_heuristics(added)
    return []


def _invoke_structured(role: AgentRole, model: str, prompt: str) -> list[Finding]:
    with span("llm.structured_review", **{"agent.role": role.value, "llm.model": model}) as current:
        structured = get_llm(model).with_structured_output(FindingBatch, include_raw=True)
        response: Any = structured.invoke([SystemMessage(content=ROLE_PROMPTS[role]), HumanMessage(content=prompt)])
        raw = response.get("raw") if isinstance(response, dict) else None
        record_token_usage(current, getattr(raw, "usage_metadata", None))
        if isinstance(response, dict) and response.get("parsing_error"):
            raise ValueError(f"structured output parse failed: {response['parsing_error']}")
        parsed = response.get("parsed") if isinstance(response, dict) else response
        if isinstance(parsed, dict):
            parsed = FindingBatch(**parsed)
        if not isinstance(parsed, FindingBatch):
            raise ValueError(f"unexpected structured output type: {type(parsed).__name__}")
        current.set_attribute("review.findings", len(parsed.findings))
        return _to_findings(parsed, role)


def run_structured_review(
    role: AgentRole,
    diff: str,
    model: str | None = None,
    extra_context: str = "",
    max_findings: int = 20,
) -> list[Finding]:
    """Return structured findings for one specialist."""
    offline = heuristic_findings(diff, role)
    if not llm_available() or not diff.strip():
        return offline[:max_findings]

    truncated = len(diff) > MAX_DIFF_CHARS
    prompt = (
        "Return only structured findings for the diff below. Do not invent issues. "
        "If nothing material, return an empty findings list. "
        "file_path and line_start must refer to the new side of the diff.\n"
        f"{UNTRUSTED_CONTENT_NOTE}\n"
        + (f"The diff was truncated to its first {MAX_DIFF_CHARS} characters.\n" if truncated else "")
        + f"{extra_context}\n\n"
        + fence(diff[:MAX_DIFF_CHARS], "diff")
    )
    for candidate in candidate_models(model):
        try:
            llm_findings = _invoke_structured(role, candidate, prompt)
        except Exception as exc:  # noqa: BLE001 - any provider failure moves to the next model
            log.warning("%s review failed on %s: %s", role.value, candidate, exc)
            continue
        if truncated:
            # The model never saw the tail; keep heuristic coverage of it (synthesize dedupes).
            return (llm_findings + offline)[:max_findings]
        return (llm_findings or offline)[:max_findings]
    return offline[:max_findings]
