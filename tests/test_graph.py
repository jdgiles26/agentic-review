"""Review graph behaviour (offline, no live LLM)."""

import time

from graphs.review_graph import plan_specialists, run_review
from memory.store import get_review
from schemas.models import AgentRole, ReviewCategory, ReviewRequest

LEAKY_DIFF = """--- a/src/auth.py
+++ b/src/auth.py
@@ -10,1 +10,3 @@
     user = load(user)
+    print(password)
+    run = eval(cmd)
"""


def test_run_review_empty_diff():
    result = run_review(ReviewRequest(diff=""))
    assert result.status == "completed"
    assert result.request_id
    assert result.duration_ms is not None
    assert result.recommendation == "approve"
    assert result.findings == []


def test_offline_review_is_fast():
    """No network calls are attempted without an API key."""
    start = time.perf_counter()
    run_review(ReviewRequest(diff=LEAKY_DIFF))
    assert time.perf_counter() - start < 2.0


def test_run_review_with_focus_limits_specialists():
    result = run_review(ReviewRequest(diff="+ print('hello')\n", focus=[ReviewCategory.SECURITY]))
    assert result.status == "completed"
    assert AgentRole.SECURITY_SPECIALIST in result.agents_involved
    assert AgentRole.STYLE_LINTER not in result.agents_involved
    assert AgentRole.SUPERVISOR in result.agents_involved


def test_plan_without_focus_runs_every_specialist():
    plan = plan_specialists([])
    assert set(plan) == {
        AgentRole.CODE_REVIEWER,
        AgentRole.SECURITY_SPECIALIST,
        AgentRole.ARCHITECTURE_REVIEWER,
        AgentRole.TEST_SPECIALIST,
        AgentRole.STYLE_LINTER,
        AgentRole.PR_SPECIALIST,
    }


def test_critical_finding_requests_changes_and_findings_sorted():
    result = run_review(ReviewRequest(diff=LEAKY_DIFF))
    assert result.recommendation == "request_changes"
    assert result.findings[0].severity.value == "critical"
    severities = [f.severity.value for f in result.findings]
    order = ["critical", "high", "medium", "low", "info"]
    assert severities == sorted(severities, key=order.index)


def test_findings_are_deduplicated():
    result = run_review(ReviewRequest(diff=LEAKY_DIFF))
    keys = [(f.title, f.file_path, f.line_start) for f in result.findings]
    assert len(keys) == len(set(keys))


def test_max_findings_caps_result():
    body = "".join(f"+v{i} = eval(x)\n" for i in range(30))
    diff = f"--- a/m.py\n+++ b/m.py\n@@ -0,0 +1,30 @@\n{body}"
    result = run_review(ReviewRequest(diff=diff, max_findings=5))
    assert len(result.findings) == 5


def test_pr_metadata_reaches_pr_specialist():
    result = run_review(ReviewRequest(diff="+x = 1\n", title="Add x", body="", additions=900, deletions=10))
    kinds = {a.action for a in result.actions}
    assert "label" in kinds  # size/XL
    assert "comment" in kinds  # thin description


def test_trajectory_is_recorded_for_replay():
    result = run_review(ReviewRequest(diff=LEAKY_DIFF))
    roles = {m.role for m in result.raw_messages}
    assert AgentRole.SUPERVISOR in roles
    assert AgentRole.SECURITY_SPECIALIST in roles


def test_review_is_persisted():
    result = run_review(ReviewRequest(diff="+    print(password)\n"))
    saved = get_review(result.request_id)
    assert saved is not None
    assert saved["request_id"] == result.request_id
    assert saved["recommendation"] == result.recommendation


def test_failing_specialist_marks_review_partial(monkeypatch):
    from graphs import review_graph

    def boom(*_args, **_kwargs):
        raise RuntimeError("specialist crashed")

    monkeypatch.setitem(review_graph.RUNNERS, AgentRole.STYLE_LINTER, boom)
    result = run_review(ReviewRequest(diff=LEAKY_DIFF))
    assert result.status == "partial"
    assert any("specialist crashed" in m.content for m in result.raw_messages)
    assert result.findings  # other specialists still reported


def _state(findings, max_findings=50):
    return {"request": {"max_findings": max_findings}, "findings": findings}


def _f(severity, confidence, title="Same", line=1):
    return {
        "id": f"{severity}-{confidence}",
        "category": "security",
        "severity": severity,
        "title": title,
        "description": "d",
        "file_path": "a.py",
        "line_start": line,
        "confidence": confidence,
    }


def test_dedupe_keeps_most_severe_duplicate():
    from graphs.review_graph import _synthesize_node

    out = _synthesize_node(_state([_f("critical", 0.6), _f("low", 0.9)]))
    assert [f["severity"] for f in out["final_findings"]] == ["critical"]
    assert out["recommendation"] == "request_changes"


def test_verdict_uses_all_findings_not_the_capped_list():
    from graphs.review_graph import _synthesize_node

    highs = [_f("high", 0.8, title=f"H{i}", line=i) for i in range(4)]
    out = _synthesize_node(_state(highs, max_findings=2))
    assert len(out["final_findings"]) == 2
    assert out["recommendation"] == "request_changes"
    assert "4 findings" in out["summary"]
