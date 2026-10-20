"""Backwards-compatibility: capabilities present on main or the merged feature branch still work."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from schemas.models import AgentRole, ReviewCategory, ReviewRequest


def test_schemas_package_reexports_models():
    from schemas import Finding, ReviewRequest, ReviewResult, Severity  # noqa: F401


def test_old_specialist_module_path_still_imports():
    from agents.specialists.tests import run_test_review

    assert callable(run_test_review)


def test_analyze_diff_tool_returns_structured_findings():
    from agents.reviewers.code_reviewer import analyze_diff

    out = json.loads(analyze_diff.invoke({"diff": "+run = eval(cmd)\n", "focus": "security"}))
    assert out["focus"] == "security"
    assert out["findings"][0]["title"] == "Dynamic code execution"


def test_analyze_diff_rejects_unknown_focus():
    from agents.reviewers.code_reviewer import analyze_diff

    with pytest.raises(ValueError):
        analyze_diff.invoke({"diff": "+x", "focus": "nonsense"})


def test_thread_id_checkpoint_roundtrip():
    from graphs.review_graph import run_review
    from memory.store import load_checkpoint

    result = run_review(ReviewRequest(diff="+run = eval(cmd)\n"), thread_id="thread-42")
    saved = load_checkpoint("thread-42")
    assert saved["recommendation"] == result.recommendation
    assert saved["request_id"] == result.request_id
    assert saved["findings"][0]["title"] == "Dynamic code execution"


def test_graph_accepts_a_checkpointer():
    from langgraph.checkpoint.memory import MemorySaver

    from graphs.review_graph import build_review_graph

    saver = MemorySaver()
    graph = build_review_graph(checkpointer=saver)
    config = {"configurable": {"thread_id": "t1"}}
    graph.invoke({"request": ReviewRequest(diff="+x = 1\n").model_dump(mode="json")}, config=config)
    assert graph.get_state(config).values["recommendation"] == "approve"
    assert build_review_graph() is build_review_graph()  # default graph stays cached


def test_plain_source_code_is_still_reviewed():
    from graphs.review_graph import run_review

    code = "import os\nAPI_KEY = 'sk-live-123'\nresult = eval(user_input)\n"
    titles = {f.title for f in run_review(ReviewRequest(diff=code)).findings}
    assert {"Dynamic code execution", "Hardcoded secret"} <= titles


def test_security_issues_found_even_when_focus_excludes_security():
    from graphs.review_graph import run_review

    result = run_review(ReviewRequest(diff="+run = eval(cmd)\n", focus=[ReviewCategory.CORRECTNESS]))
    assert AgentRole.SECURITY_SPECIALIST not in result.agents_involved
    assert [f.title for f in result.findings] == ["Dynamic code execution"]


def test_heuristic_coverage_not_narrowed():
    from agents.specialists.structured import heuristic_findings

    assert heuristic_findings("+x = 1  # xxx later\n", AgentRole.STYLE_LINTER)
    big = "+" + "a" * 8100 + "\n"
    assert heuristic_findings(big, AgentRole.ARCHITECTURE_REVIEWER)
    in_test = "--- a/tests/t.py\n+++ b/tests/t.py\n@@ -0,0 +1 @@\n+password = 'hunter2'\n"
    assert heuristic_findings(in_test, AgentRole.SECURITY_SPECIALIST)


class _FakeGitHub:
    def __init__(self, token=None):
        self.available = True

    def get_pr_context(self, owner, repo, number):
        return {"title": "t", "body": "b", "files": ["a.py"], "diff": "+x = 1\n"}


@pytest.fixture
def client():
    from api.main import app

    with TestClient(app) as c:
        yield c


def test_github_review_accepts_focus_in_json_body(client, monkeypatch):
    import api.main

    monkeypatch.setattr(api.main, "GitHubClient", _FakeGitHub)
    res = client.post("/v1/review/github/acme/widgets/1", json=["security"])
    assert res.status_code == 200, res.text
    assert "style_linter" not in res.json()["agents_involved"]


def test_webhook_queued_status_is_200(client, monkeypatch):
    monkeypatch.setenv("ALLOW_UNSIGNED_WEBHOOKS", "true")
    event = {"action": "opened", "number": 1, "pull_request": {"number": 1}, "repository": {"full_name": "a/b"}}
    res = client.post("/v1/webhooks/github", json=event)
    assert res.status_code == 200
    assert res.json()["status"] == "queued"


def test_webhook_body_schema_documented(client):
    spec = client.get("/openapi.json").json()
    body = spec["paths"]["/v1/webhooks/github"]["post"]["requestBody"]
    assert "action" in json.dumps(body)


def test_job_queue_size_configurable(monkeypatch):
    from api import jobs

    monkeypatch.setenv("REVIEW_MAX_JOBS", "1000")
    assert jobs.max_jobs() == 1000
    for bad in ("0", "-5", "lots"):
        monkeypatch.setenv("REVIEW_MAX_JOBS", bad)
        assert jobs.max_jobs() == jobs.MAX_JOBS
