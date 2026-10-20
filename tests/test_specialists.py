"""Tests for structured specialists, memory, and the job queue contract."""

from agents.specialists.security import run_security_review
from graphs.review_graph import run_review
from memory.store import get_review
from schemas.models import ReviewRequest


def test_security_heuristic_finds_logged_secret(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    findings = run_security_review("+    print(password)\n")
    titles = {f.title for f in findings}
    assert "Secret logged" in titles


def test_review_is_checkpointed(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("REVIEW_DB", str(tmp_path / "reviews.db"))
    result = run_review(ReviewRequest(diff="+    print(password)\n"))
    saved = get_review(result.request_id)
    assert saved is not None
    assert saved["request_id"] == result.request_id
    assert saved["recommendation"] in {"approve", "request_changes", "comment"}
