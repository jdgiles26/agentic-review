"""HTTP API contract tests (offline)."""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from api import jobs
from api.main import app

PR_EVENT = {
    "action": "opened",
    "number": 7,
    "pull_request": {"number": 7, "title": "Add login"},
    "repository": {"full_name": "acme/widgets"},
}


class FakeGitHub:
    """Stands in for tools.github.client.GitHubClient."""

    comments: list[tuple[str, str, int, str]] = []

    def __init__(self, token: str | None = None):
        self.available = True

    def get_pr_context(self, owner, repo, number):
        return {
            "title": "Add login",
            "body": "Implements login with a test plan and risk notes for reviewers.",
            "additions": 3,
            "deletions": 1,
            "files": ["src/auth.py"],
            "diff": "--- a/src/auth.py\n+++ b/src/auth.py\n@@ -1 +1,2 @@\n x = 1\n+print(password)\n",
        }

    def post_comment(self, owner, repo, number, body):
        FakeGitHub.comments.append((owner, repo, number, body))
        return "https://github.com/acme/widgets/pull/7#issuecomment-1"


@pytest.fixture
def client():
    FakeGitHub.comments = []
    with TestClient(app) as c:
        yield c


@pytest.fixture
def fake_github(monkeypatch):
    import api.main

    monkeypatch.setattr(jobs, "GitHubClient", FakeGitHub)
    monkeypatch.setattr(api.main, "GitHubClient", FakeGitHub)
    return FakeGitHub


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _wait_for_job(client: TestClient, job_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/v1/jobs/{job_id}").json()
        if job["status"] in {"completed", "failed"}:
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish")


def test_health_reports_integrations(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert "style_linter" in body["agents_ready"]
    assert body["llm_configured"] is False
    assert body["github_configured"] is False


def test_review_diff_and_lookup(client):
    res = client.post("/v1/review", json={"diff": "+    print(password)\n"})
    assert res.status_code == 200
    result = res.json()
    assert any(f["title"] == "Secret logged" for f in result["findings"])

    saved = client.get(f"/v1/reviews/{result['request_id']}")
    assert saved.status_code == 200
    assert saved.json()["request_id"] == result["request_id"]

    listed = client.get("/v1/reviews").json()
    assert listed[0]["request_id"] == result["request_id"]


def test_review_rejects_invalid_focus(client):
    res = client.post("/v1/review", json={"diff": "+x", "focus": ["nonsense"]})
    assert res.status_code == 422


def test_unknown_review_is_404(client):
    assert client.get("/v1/reviews/nope").status_code == 404
    assert client.get("/v1/jobs/nope").status_code == 404


def test_github_review_requires_token(client):
    res = client.post("/v1/review/github/acme/widgets/7")
    assert res.status_code == 503


def test_github_review_with_focus_query(client, fake_github):
    res = client.post("/v1/review/github/acme/widgets/7?focus=security")
    assert res.status_code == 200
    body = res.json()
    assert "security_specialist" in body["agents_involved"]
    assert "style_linter" not in body["agents_involved"]
    assert body["findings"][0]["file_path"] == "src/auth.py"


def test_github_review_rejects_bad_focus(client, fake_github):
    assert client.post("/v1/review/github/acme/widgets/7?focus=bogus").status_code == 422


def test_webhook_refused_without_secret_by_default(client):
    res = client.post("/v1/webhooks/github", json=PR_EVENT, headers={"X-GitHub-Event": "pull_request"})
    assert res.status_code == 503


def test_webhook_rejects_bad_signature(client, monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "s3cret")
    body = json.dumps(PR_EVENT).encode()
    res = client.post(
        "/v1/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": _sign("wrong", body),
            "Content-Type": "application/json",
        },
    )
    assert res.status_code == 401


def test_webhook_ping(client, monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "s3cret")
    body = json.dumps({"zen": "Keep it simple."}).encode()
    res = client.post(
        "/v1/webhooks/github",
        content=body,
        headers={"X-GitHub-Event": "ping", "X-Hub-Signature-256": _sign("s3cret", body)},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "pong"


def test_webhook_ignores_other_actions(client, monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "s3cret")
    body = json.dumps({**PR_EVENT, "action": "closed"}).encode()
    res = client.post(
        "/v1/webhooks/github",
        content=body,
        headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": _sign("s3cret", body)},
    )
    assert res.json()["status"] == "ignored"


def test_signed_webhook_queues_review_without_posting_by_default(client, monkeypatch, fake_github):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "s3cret")
    body = json.dumps(PR_EVENT).encode()
    res = client.post(
        "/v1/webhooks/github",
        content=body,
        headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": _sign("s3cret", body)},
    )
    assert res.status_code == 200
    queued = res.json()
    assert queued["status"] == "queued"
    assert queued["repo"] == "acme/widgets"

    job = _wait_for_job(client, queued["job_id"])
    assert job["status"] == "completed", job["error"]
    assert job["result"]["recommendation"] in {"approve", "comment", "request_changes"}
    assert job["comment_url"] is None
    assert fake_github.comments == []  # HITL gate: posting is opt-in

    assert any(j["id"] == queued["job_id"] for j in client.get("/v1/jobs").json())


def test_comment_posted_when_enabled(client, monkeypatch, fake_github):
    monkeypatch.setenv("ALLOW_UNSIGNED_WEBHOOKS", "true")
    monkeypatch.setenv("REVIEW_POST_COMMENTS", "true")
    res = client.post("/v1/webhooks/github", json=PR_EVENT, headers={"X-GitHub-Event": "pull_request"})
    job = _wait_for_job(client, res.json()["job_id"])
    assert job["status"] == "completed", job["error"]
    assert job["comment_url"].endswith("issuecomment-1")
    ((_, _, number, comment),) = fake_github.comments
    assert number == 7
    assert "Secret logged" in comment


def test_failed_job_reports_error(client, monkeypatch):
    monkeypatch.setenv("ALLOW_UNSIGNED_WEBHOOKS", "1")
    res = client.post("/v1/webhooks/github", json=PR_EVENT, headers={"X-GitHub-Event": "pull_request"})
    job = _wait_for_job(client, res.json()["job_id"])
    assert job["status"] == "failed"
    assert "GITHUB_TOKEN" in job["error"]


def test_job_registry_is_bounded():
    jobs.reset()
    try:
        for i in range(jobs.MAX_JOBS + 5):
            job = jobs.Job(id=f"j{i}", kind="github_pr_review", payload={}, status="completed")
            jobs._register(job)
        assert len(jobs.list_jobs(limit=10_000)) == jobs.MAX_JOBS
        assert jobs.get_job("j0") is None
    finally:
        jobs.reset()


def test_webhook_rejects_non_object_payload(client, monkeypatch):
    monkeypatch.setenv("ALLOW_UNSIGNED_WEBHOOKS", "true")
    for body in (b"[1, 2]", b"not json"):
        res = client.post("/v1/webhooks/github", content=body, headers={"X-GitHub-Event": "pull_request"})
        assert res.status_code == 400


def test_queue_rejects_when_full(monkeypatch):
    import asyncio

    async def scenario():
        jobs.reset()
        monkeypatch.setattr(jobs, "MAX_JOBS", 2)
        jobs._queue = asyncio.Queue(maxsize=jobs.MAX_JOBS)
        try:
            jobs.enqueue("github_pr_review", {})
            jobs.enqueue("github_pr_review", {})
            with pytest.raises(RuntimeError, match="full"):
                jobs.enqueue("github_pr_review", {})
            assert len(jobs.list_jobs(limit=100)) == 2
        finally:
            jobs._queue = None
            jobs.reset()

    asyncio.run(scenario())
