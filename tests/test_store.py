"""Review history store."""

from memory.store import db_path, get_review, list_reviews, save_review
from schemas.models import ReviewRequest, ReviewResult


def _result(request_id: str) -> ReviewResult:
    return ReviewResult(
        request_id=request_id,
        status="completed",
        summary="ok",
        findings=[],
        agents_involved=[],
        recommendation="approve",
        score=9.0,
    )


def test_db_path_follows_env_at_call_time(monkeypatch, tmp_path):
    target = tmp_path / "nested" / "other.db"
    monkeypatch.setenv("REVIEW_DB", str(target))
    assert db_path() == target
    save_review(ReviewRequest(diff="+x"), _result("r1"))
    assert target.exists()


def test_roundtrip_and_listing():
    save_review(ReviewRequest(diff="+a"), _result("r1"))
    save_review(ReviewRequest(diff="+b"), _result("r2"))
    assert get_review("r1")["summary"] == "ok"
    assert get_review("missing") is None
    listed = list_reviews(limit=10)
    assert [r["request_id"] for r in listed] == ["r2", "r1"]
    assert list_reviews(limit=1)[0]["request_id"] == "r2"
