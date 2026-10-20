"""Shared fixtures: every test runs offline against an isolated review database."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _offline_env(monkeypatch, tmp_path):
    for var in (
        "OPENAI_API_KEY",
        "GITHUB_TOKEN",
        "GITHUB_WEBHOOK_SECRET",
        "ALLOW_UNSIGNED_WEBHOOKS",
        "REVIEW_POST_COMMENTS",
        "REVIEW_FALLBACK_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("REVIEW_DB", str(tmp_path / "reviews.db"))
