"""LangGraph multi-agent review pipeline.

supervisor ──┬─> code_reviewer ─────────┐
             ├─> security_specialist ───┤
             ├─> architecture_reviewer ─┼─> synthesize ─> END
             ├─> test_specialist ───────┤
             ├─> style_linter ──────────┤
             └─> pr_specialist ─────────┘

The supervisor plans which specialists to run from the request focus; planned
specialists run in parallel in one superstep and their findings and notes are
merged by list reducers. A crashing specialist is isolated: the review still
completes, marked "partial".
"""

from __future__ import annotations

import logging
import operator
import time
import uuid
from collections.abc import Callable
from functools import lru_cache
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, StateGraph

from agents.reviewers.code_reviewer import run_code_review
from agents.specialists.architecture import run_architecture_review
from agents.specialists.pr_specialist import evaluate_pr
from agents.specialists.security import run_security_review
from agents.specialists.style import run_style_review
from agents.specialists.testing import run_test_review
from memory.store import save_review
from schemas.models import (
    AgentMessage,
    AgentRole,
    Finding,
    PRAction,
    ReviewCategory,
    ReviewRequest,
    ReviewResult,
)
from tools.diff import changed_files
from tools.tracing import span

log = logging.getLogger(__name__)

SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"]

Runner = Callable[..., list[Finding]]

# Looked up at call time so tests (and plugins) can swap a runner.
RUNNERS: dict[AgentRole, Runner] = {
    AgentRole.CODE_REVIEWER: run_code_review,
    AgentRole.SECURITY_SPECIALIST: run_security_review,
    AgentRole.ARCHITECTURE_REVIEWER: run_architecture_review,
    AgentRole.TEST_SPECIALIST: run_test_review,
    AgentRole.STYLE_LINTER: run_style_review,
}

# Which specialist owns each focus category. The code reviewer always runs.
FOCUS_OWNERS: dict[ReviewCategory, AgentRole] = {
    ReviewCategory.SECURITY: AgentRole.SECURITY_SPECIALIST,
    ReviewCategory.DEPENDENCIES: AgentRole.SECURITY_SPECIALIST,
    ReviewCategory.ARCHITECTURE: AgentRole.ARCHITECTURE_REVIEWER,
    ReviewCategory.PERFORMANCE: AgentRole.ARCHITECTURE_REVIEWER,
    ReviewCategory.TEST_COVERAGE: AgentRole.TEST_SPECIALIST,
    ReviewCategory.STYLE: AgentRole.STYLE_LINTER,
    ReviewCategory.DOCUMENTATION: AgentRole.STYLE_LINTER,
    ReviewCategory.CORRECTNESS: AgentRole.CODE_REVIEWER,
}


class ReviewState(TypedDict, total=False):
    request: dict[str, Any]
    plan: list[str]
    findings: Annotated[list[dict[str, Any]], operator.add]
    notes: Annotated[list[dict[str, Any]], operator.add]
    errors: Annotated[list[str], operator.add]
    final_findings: list[dict[str, Any]]  # deduplicated, ranked, capped by synthesize
    actions: list[dict[str, Any]]
    pr_summary: str
    summary: str
    recommendation: str
    score: float


def _note(role: AgentRole, content: str, **metadata: Any) -> dict[str, Any]:
    return {"role": role.value, "content": content, "metadata": metadata}


def plan_specialists(focus: list[str] | list[ReviewCategory]) -> list[AgentRole]:
    """Specialists to run for a focus list; an empty focus runs everyone."""
    if not focus:
        return [*RUNNERS, AgentRole.PR_SPECIALIST]
    wanted = {AgentRole.CODE_REVIEWER, AgentRole.PR_SPECIALIST}
    wanted.update(FOCUS_OWNERS[ReviewCategory(c)] for c in focus)
    return [role for role in [*RUNNERS, AgentRole.PR_SPECIALIST] if role in wanted]


def _supervisor_node(state: ReviewState) -> dict[str, Any]:
    plan = plan_specialists(state["request"].get("focus") or [])
    names = [r.value for r in plan]
    return {
        "plan": names,
        "notes": [_note(AgentRole.SUPERVISOR, f"Plan: invoke {', '.join(names)}", plan=names)],
    }


def _route(state: ReviewState) -> list[str]:
    return state["plan"]


def _specialist_node(role: AgentRole) -> Callable[[ReviewState], dict[str, Any]]:
    def node(state: ReviewState) -> dict[str, Any]:
        req = state["request"]
        with span("review.node", **{"agent.role": role.value}) as current:
            try:
                produced = RUNNERS[role](
                    req.get("diff") or "", model=req.get("model"), max_findings=req["max_findings"]
                )
            except Exception as exc:  # noqa: BLE001 - isolate one specialist's failure
                log.exception("%s failed", role.value)
                current.set_attribute("review.error", str(exc))
                return {
                    "errors": [f"{role.value}: {exc}"],
                    "notes": [_note(role, f"Failed: {exc}", error=True)],
                }
            current.set_attribute("review.findings", len(produced))
        findings = [f.model_dump(mode="json") for f in produced]
        return {
            "findings": findings,
            "notes": [_note(role, f"Produced {len(findings)} findings", findings=findings)],
        }

    node.__name__ = f"{role.value}_node"
    return node


def _pr_specialist_node(state: ReviewState) -> dict[str, Any]:
    req = state["request"]
    role = AgentRole.PR_SPECIALIST
    with span("review.node", **{"agent.role": role.value}):
        try:
            summary, actions = evaluate_pr(
                title=req.get("title") or "Untitled PR",
                body=req.get("body"),
                changed_files=req.get("files") or changed_files(req.get("diff") or ""),
                additions=req.get("additions") or 0,
                deletions=req.get("deletions") or 0,
                model=req.get("model"),
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("pr_specialist failed")
            return {"errors": [f"{role.value}: {exc}"], "notes": [_note(role, f"Failed: {exc}", error=True)]}
    action_dicts = [a.model_dump(mode="json") for a in actions]
    return {
        "pr_summary": summary,
        "actions": action_dicts,
        "notes": [_note(role, summary or f"{len(actions)} PR hygiene actions", actions=action_dicts)],
    }


def _dedupe_key(f: dict[str, Any]) -> tuple[Any, ...]:
    return (f.get("category"), f.get("title", "").strip().lower(), f.get("file_path"), f.get("line_start"))


def _rank(f: dict[str, Any]) -> tuple[int, float]:
    sev = f.get("severity")
    return (SEVERITY_ORDER.index(sev) if sev in SEVERITY_ORDER else len(SEVERITY_ORDER), -f.get("confidence", 0))


def _score(findings: list[dict[str, Any]]) -> tuple[str, float]:
    counts = {s: sum(1 for f in findings if f.get("severity") == s) for s in SEVERITY_ORDER}
    if counts["critical"]:
        return "request_changes", 3.0
    if counts["high"] > 2:
        return "request_changes", 5.0
    if counts["high"]:
        return "comment", 6.0
    if findings:
        return "comment", 7.5
    return "approve", 9.0


def _synthesize_node(state: ReviewState) -> dict[str, Any]:
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for f in state.get("findings") or []:
        key = _dedupe_key(f)
        # Most severe wins, then most confident, so dedupe never downgrades a finding.
        if key not in unique or _rank(f) < _rank(unique[key]):
            unique[key] = f
    every = sorted(unique.values(), key=_rank)
    ranked = every[: state["request"]["max_findings"]]
    # The verdict covers every finding; max_findings only limits what is reported.
    rec, score = _score(every)

    n_critical = sum(1 for f in every if f.get("severity") == "critical")
    n_high = sum(1 for f in every if f.get("severity") == "high")
    shown = f", showing {len(ranked)}" if len(ranked) < len(every) else ""
    summary = state.get("pr_summary") or (
        f"Review complete. {len(every)} findings ({n_critical} critical, {n_high} high{shown})."
    )
    errors = state.get("errors") or []
    if errors:
        summary += f" {len(errors)} specialist(s) failed; results are partial."
    return {
        "final_findings": ranked,
        "summary": summary,
        "recommendation": rec,
        "score": score,
        "notes": [_note(AgentRole.SUPERVISOR, f"Recommendation: {rec} ({score})")],
    }


@lru_cache(maxsize=1)
def build_review_graph():
    """Compile the graph once; it is stateless and safe to share across threads."""
    g = StateGraph(ReviewState)
    g.add_node("supervisor", _supervisor_node)
    specialists = [r.value for r in RUNNERS]
    for role in RUNNERS:
        g.add_node(role.value, _specialist_node(role))
    g.add_node(AgentRole.PR_SPECIALIST.value, _pr_specialist_node)
    g.add_node("synthesize", _synthesize_node)

    g.set_entry_point("supervisor")
    g.add_conditional_edges("supervisor", _route, [*specialists, AgentRole.PR_SPECIALIST.value])
    for name in [*specialists, AgentRole.PR_SPECIALIST.value]:
        g.add_edge(name, "synthesize")
    g.add_edge("synthesize", END)
    return g.compile()


def run_review(request: ReviewRequest) -> ReviewResult:
    """Public entry point used by API, CLI, and background jobs."""
    start = time.perf_counter()
    request_id = str(uuid.uuid4())
    with span("review.run", **{"review.request_id": request_id}) as current:
        final = build_review_graph().invoke({"request": request.model_dump(mode="json")})
        duration_ms = int((time.perf_counter() - start) * 1000)

        notes = final.get("notes") or []
        plan = [AgentRole(a) for a in final.get("plan") or []]
        errors = final.get("errors") or []
        result = ReviewResult(
            request_id=request_id,
            status="partial" if errors else "completed",
            summary=final.get("summary", ""),
            findings=[Finding(**f) for f in final.get("final_findings") or []],
            agents_involved=[AgentRole.SUPERVISOR, *plan],
            score=final.get("score"),
            recommendation=final.get("recommendation"),
            actions=[PRAction(**a) for a in final.get("actions") or []],
            raw_messages=[
                AgentMessage(
                    role=AgentRole(n["role"]),
                    content=n["content"],
                    findings=[Finding(**f) for f in n.get("metadata", {}).get("findings", [])],
                    metadata={k: v for k, v in n.get("metadata", {}).items() if k != "findings"},
                )
                for n in notes
            ],
            duration_ms=duration_ms,
        )
        current.set_attribute("review.findings", len(result.findings))
        current.set_attribute("review.recommendation", result.recommendation or "")

    try:
        save_review(request, result)
    except Exception:  # noqa: BLE001 - persistence must never fail a review
        log.exception("Failed to persist review %s", request_id)
    return result
