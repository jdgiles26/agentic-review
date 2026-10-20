"""GitHub PR → ReviewRequest mapping and PR comment formatting."""

from schemas.models import (
    AgentRole,
    Finding,
    PRAction,
    ReviewCategory,
    ReviewResult,
    Severity,
)
from tools.github.pr_context import build_pr_review_request, format_review_comment


class Source:
    def get_pr_context(self, owner, repo, number):
        return {
            "title": "Add login",
            "body": "Body",
            "additions": 5,
            "deletions": 2,
            "files": ["a.py"],
            "head_sha": "abc123",
            "diff": "--- a/a.py\n+++ b/a.py\n@@ -0,0 +1 @@\n+x = 1\n",
        }


def test_build_request_carries_pr_metadata():
    req = build_pr_review_request(Source(), "acme", "widgets", 3, focus=[ReviewCategory.SECURITY])
    assert req.repo_url == "https://github.com/acme/widgets"
    assert (req.pr_number, req.title, req.body, req.additions, req.deletions) == (
        3,
        "Add login",
        "Body",
        5,
        2,
    )
    assert req.commit_sha == "abc123"
    assert req.focus == [ReviewCategory.SECURITY]


def test_comment_lists_findings_with_locations_and_escapes_pipes():
    result = ReviewResult(
        request_id="rid",
        status="completed",
        summary="One issue.",
        findings=[
            Finding(
                id="f1",
                category=ReviewCategory.SECURITY,
                severity=Severity.HIGH,
                title="a | b",
                description="d",
                file_path="a.py",
                line_start=4,
            )
        ],
        agents_involved=[AgentRole.SUPERVISOR],
        recommendation="comment",
        score=6.0,
        actions=[PRAction(action="comment", body="Expand the description.")],
    )
    body = format_review_comment(result)
    assert "`a.py:4`" in body
    assert "a \\| b" in body
    assert "> Expand the description." in body
    assert "`rid`" in body


def test_client_builds_diff_in_one_pass(monkeypatch):
    from types import SimpleNamespace

    from tools.github.client import GitHubClient

    files = [
        SimpleNamespace(filename="src/new.py", previous_filename="src/old.py", patch="@@ -1 +1 @@\n-a\n+b"),
        SimpleNamespace(filename="logo.png", previous_filename=None, patch=None),
    ]
    calls = {"get_files": 0}

    def get_files():
        calls["get_files"] += 1
        return files

    pr = SimpleNamespace(
        title="T",
        body="B",
        state="open",
        user=SimpleNamespace(login="dev"),
        additions=1,
        deletions=1,
        changed_files=2,
        head=SimpleNamespace(sha="h"),
        base=SimpleNamespace(ref="main"),
        html_url="https://github.com/acme/widgets/pull/1",
        get_files=get_files,
    )
    client = GitHubClient(token="t")
    monkeypatch.setattr(client, "_pull", lambda *a: pr)
    ctx = client.get_pr_context("acme", "widgets", 1)
    assert calls["get_files"] == 1
    assert ctx["files"] == ["src/new.py", "logo.png"]
    assert ctx["diff"] == "--- a/src/old.py\n+++ b/src/new.py\n@@ -1 +1 @@\n-a\n+b"
    assert ctx["head_sha"] == "h"


def test_client_without_token_is_unavailable():
    import pytest

    from tools.github.client import GitHubClient

    client = GitHubClient()
    assert client.available is False
    with pytest.raises(RuntimeError, match="GITHUB_TOKEN"):
        client.get_pr_context("a", "b", 1)
