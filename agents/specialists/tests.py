"""Backwards-compatible import path; the test-coverage specialist lives in `testing`."""

from agents.specialists.testing import run_test_review

__all__ = ["run_test_review"]
