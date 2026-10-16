"""LangGraph multi-agent review pipeline with specialist nodes and checkpoints."""

from __future__ import annotations

import time
import uuid
from typing import TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from agents.base import AgentRole
from agents.reviewers.code_reviewer import run_code_review
from agents.specialists.architecture import run_architecture_review
from agents.specialists.pr_specialist import evaluate_pr
from agents.specialists.security import run_security_review
from agents.specialists.style import run_style_review
from agents.specialists.tests import run_test_review
from memory.store import save_checkpoint, save_review
from schemas.models import Finding, ReviewRequest, ReviewResult


class ReviewState(TypedDict):
    request: dict
    messages: list
    findings: list[dict]
    agents_run: list[str]
    summary: str
    recommendation: str
    score: float | None
    thread_id: str


def _note(role: str, content: str) -> dict:
    return {"role": "assistant", "content": f"[{role}] {content}"}


def _supervisor_node(state: ReviewState) -> dict:
    req = state["request"]
    focus = req.get("focus") or []
    plan = [AgentRole.CODE_REVIEWER.value, AgentRole.PR_SPECIALIST.value]
    if "security" in focus or not focus:
        plan.append(AgentRole.SECURITY_SPECIALIST.value)
    if "architecture" in focus or not focus:
        plan.append(AgentRole.ARCHITECTURE_REVIEWER.value)
    if "test_coverage" in focus or not focus:
        plan.append(AgentRole.TEST_SPECIALIST.value)
    if "style" in focus or not focus:
        plan.append(AgentRole.STYLE_LINTER.value)
    return {
        "agents_run": plan,
        "messages": state.get("messages", []) + [_note("supervisor", f"Plan: invoke {', '.join(plan)}")],
    }


def _specialists_node(state: ReviewState) -> dict:
    req = state["request"]
    diff = req.get("diff") or ""
    model = req.get("model")
    plan = set(state.get("agents_run") or [])
    findings = list(state.get("findings") or [])
    messages = list(state.get("messages") or [])

    runners = [
        (AgentRole.CODE_REVIEWER, run_code_review),
        (AgentRole.SECURITY_SPECIALIST, run_security_review),
        (AgentRole.ARCHITECTURE_REVIEWER, run_architecture_review),
        (AgentRole.TEST_SPECIALIST, run_test_review),
        (AgentRole.STYLE_LINTER, run_style_review),
    ]
    for role, runner in runners:
        if role.value not in plan:
            continue
        produced = runner(diff, model)
        findings.extend(f.model_dump() for f in produced)
        messages.append(_note(role.value, f"Produced {len(produced)} findings"))

    return {"findings": findings, "messages": messages}


def _pr_specialist_node(state: ReviewState) -> dict:
    req = state["request"]
    summary, actions = evaluate_pr(
        title=req.get("title") or "Untitled PR",
        body=req.get("body"),
        changed_files=req.get("files") or [],
        additions=req.get("additions", 0),
        deletions=req.get("deletions", 0),
        model=req.get("model"),
    )
    note = _note("pr_specialist", summary)
    note["metadata"] = {"actions": [a.model_dump() for a in actions]}
    return {"summary": summary, "messages": state.get("messages", []) + [note]}


def _synthesize_node(state: ReviewState) -> dict:
    findings = state.get("findings", [])
    n_critical = sum(1 for f in findings if f.get("severity") == "critical")
    n_high = sum(1 for f in findings if f.get("severity") == "high")

    if n_critical > 0:
        rec, score = "request_changes", 3.0
    elif n_high > 2:
        rec, score = "request_changes", 5.0
    elif findings:
        rec, score = "comment", 7.0
    else:
        rec, score = "approve", 9.0

    summary = state.get("summary") or (
        f"Review complete. {len(findings)} findings ({n_critical} critical, {n_high} high)."
    )
    save_checkpoint(
        state.get("thread_id") or "anonymous",
        {"findings": findings, "recommendation": rec, "score": score, "summary": summary},
    )
    return {"recommendation": rec, "score": score, "summary": summary}


_CHECKPOINTER = MemorySaver()


def build_review_graph():
    g = StateGraph(ReviewState)
    g.add_node("supervisor", _supervisor_node)
    g.add_node("specialists", _specialists_node)
    g.add_node("pr_specialist", _pr_specialist_node)
    g.add_node("synthesize", _synthesize_node)
    g.set_entry_point("supervisor")
    g.add_edge("supervisor", "specialists")
    g.add_edge("specialists", "pr_specialist")
    g.add_edge("pr_specialist", "synthesize")
    g.add_edge("synthesize", END)
    return g.compile(checkpointer=_CHECKPOINTER)


def run_review(request: ReviewRequest, thread_id: str | None = None) -> ReviewResult:
    """Public entry point used by API, CLI, and background jobs."""
    start = time.perf_counter()
    request_id = str(uuid.uuid4())
    thread = thread_id or request_id
    graph = build_review_graph()

    initial: ReviewState = {
        "request": request.model_dump(),
        "messages": [],
        "findings": [],
        "agents_run": [],
        "summary": "",
        "recommendation": "comment",
        "score": None,
        "thread_id": thread,
    }
    final = graph.invoke(initial, config={"configurable": {"thread_id": thread}})
    duration_ms = int((time.perf_counter() - start) * 1000)

    findings = [Finding(**f) for f in final.get("findings", [])]
    role_values = {r.value for r in AgentRole}
    agents = [AgentRole(a) for a in final.get("agents_run", []) if a in role_values]

    result = ReviewResult(
        request_id=request_id,
        status="completed",
        summary=final.get("summary", ""),
        findings=findings,
        agents_involved=agents or [AgentRole.CODE_REVIEWER, AgentRole.PR_SPECIALIST],
        score=final.get("score"),
        recommendation=final.get("recommendation"),
        duration_ms=duration_ms,
    )
    save_review(request, result)
    return result
