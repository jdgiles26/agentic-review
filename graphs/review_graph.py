"""LangGraph multi-agent review pipeline.

Supervisor decides which specialists to run, then synthesizes results.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from agents.base import AgentRole
from agents.reviewers.code_reviewer import run_code_review
from agents.specialists.pr_specialist import evaluate_pr
from schemas.models import Finding, ReviewRequest, ReviewResult


class ReviewState(TypedDict):
    request: dict
    messages: list
    findings: list[dict]
    agents_run: list[str]
    summary: str
    recommendation: str
    score: float | None


def _note(role: str, content: str, metadata: dict[str, Any] | None = None) -> dict:
    """Store agent notes as assistant messages so LangGraph can coerce them."""
    prefix = f"[{role}] {content}"
    extra = f" {metadata}" if metadata else ""
    return {"role": "assistant", "content": prefix + extra}


def _supervisor_node(state: ReviewState) -> dict:
    req = state["request"]
    plan = [AgentRole.CODE_REVIEWER.value, AgentRole.PR_SPECIALIST.value]
    focus = req.get("focus") or []
    if "security" in focus or not focus:
        plan.append(AgentRole.SECURITY_SPECIALIST.value)
    if "architecture" in focus:
        plan.append(AgentRole.ARCHITECTURE_REVIEWER.value)
    if "test_coverage" in focus:
        plan.append(AgentRole.TEST_SPECIALIST.value)

    return {
        "agents_run": plan,
        "messages": state.get("messages", []) + [_note("supervisor", f"Plan: invoke {', '.join(plan)}")],
    }


def _code_reviewer_node(state: ReviewState) -> dict:
    diff = state["request"].get("diff") or ""
    findings = run_code_review(diff)
    return {
        "findings": state.get("findings", []) + [f.model_dump() for f in findings],
        "messages": state.get("messages", [])
        + [_note("code_reviewer", f"Produced {len(findings)} findings")],
    }


def _pr_specialist_node(state: ReviewState) -> dict:
    req = state["request"]
    title = req.get("title") or "Untitled PR"
    body = req.get("body")
    files = req.get("files") or []
    summary, actions = evaluate_pr(
        title=title,
        body=body,
        changed_files=files,
        additions=req.get("additions", 0),
        deletions=req.get("deletions", 0),
    )
    return {
        "summary": summary,
        "messages": state.get("messages", [])
        + [_note("pr_specialist", summary, {"actions": [a.model_dump() for a in actions]})],
    }


def _synthesize_node(state: ReviewState) -> dict:
    findings = state.get("findings", [])
    n_critical = sum(1 for f in findings if f.get("severity") == "critical")
    n_high = sum(1 for f in findings if f.get("severity") == "high")

    if n_critical > 0:
        rec = "request_changes"
        score = 3.0
    elif n_high > 2:
        rec = "request_changes"
        score = 5.0
    elif findings:
        rec = "comment"
        score = 7.0
    else:
        rec = "approve"
        score = 9.0

    summary = state.get("summary") or (
        f"Review complete. {len(findings)} findings "
        f"({n_critical} critical, {n_high} high)."
    )
    return {
        "recommendation": rec,
        "score": score,
        "summary": summary,
    }


def build_review_graph():
    g = StateGraph(ReviewState)
    g.add_node("supervisor", _supervisor_node)
    g.add_node("code_reviewer", _code_reviewer_node)
    g.add_node("pr_specialist", _pr_specialist_node)
    g.add_node("synthesize", _synthesize_node)

    g.set_entry_point("supervisor")
    g.add_edge("supervisor", "code_reviewer")
    g.add_edge("code_reviewer", "pr_specialist")
    g.add_edge("pr_specialist", "synthesize")
    g.add_edge("synthesize", END)
    return g.compile()


def run_review(request: ReviewRequest) -> ReviewResult:
    """Public entry point used by API and CLI."""
    start = time.perf_counter()
    request_id = str(uuid.uuid4())
    graph = build_review_graph()

    initial: ReviewState = {
        "request": request.model_dump(),
        "messages": [],
        "findings": [],
        "agents_run": [],
        "summary": "",
        "recommendation": "comment",
        "score": None,
    }

    final = graph.invoke(initial)
    duration_ms = int((time.perf_counter() - start) * 1000)

    findings = [Finding(**f) for f in final.get("findings", [])]
    role_values = {r.value for r in AgentRole}
    agents = [AgentRole(a) for a in final.get("agents_run", []) if a in role_values]

    return ReviewResult(
        request_id=request_id,
        status="completed",
        summary=final.get("summary", ""),
        findings=findings,
        agents_involved=agents or [AgentRole.CODE_REVIEWER, AgentRole.PR_SPECIALIST],
        score=final.get("score"),
        recommendation=final.get("recommendation"),
        duration_ms=duration_ms,
    )
