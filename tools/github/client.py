"""Thin GitHub client used by tools and the PR specialist."""

from __future__ import annotations

import os
from typing import Any

from github import Auth, Github


class GitHubClient:
    def __init__(self, token: str | None = None):
        self.token = token or os.getenv("GITHUB_TOKEN")
        self._gh = Github(auth=Auth.Token(self.token)) if self.token else None

    @property
    def available(self) -> bool:
        return self._gh is not None

    def _pull(self, owner: str, repo: str, number: int):
        if not self._gh:
            raise RuntimeError("GITHUB_TOKEN not set")
        return self._gh.get_repo(f"{owner}/{repo}").get_pull(number)

    def get_pr_context(self, owner: str, repo: str, number: int) -> dict[str, Any]:
        """PR metadata plus a unified diff, fetched with one pass over the changed files."""
        pr = self._pull(owner, repo, number)
        files: list[str] = []
        parts: list[str] = []
        for f in pr.get_files():
            files.append(f.filename)
            if f.patch:  # binary or very large files have no patch
                old = f.previous_filename or f.filename
                parts.append(f"--- a/{old}\n+++ b/{f.filename}\n{f.patch}")
        return {
            "title": pr.title,
            "body": pr.body,
            "state": pr.state,
            "user": pr.user.login if pr.user else None,
            "additions": pr.additions,
            "deletions": pr.deletions,
            "changed_files": pr.changed_files,
            "head_sha": pr.head.sha,
            "base_ref": pr.base.ref,
            "html_url": pr.html_url,
            "files": files,
            "diff": "\n".join(parts),
        }

    def get_pr(self, owner: str, repo: str, number: int) -> dict[str, Any]:
        context = self.get_pr_context(owner, repo, number)
        context.pop("diff")
        return context

    def get_pr_diff(self, owner: str, repo: str, number: int) -> str:
        return self.get_pr_context(owner, repo, number)["diff"]

    def post_comment(self, owner: str, repo: str, number: int, body: str) -> str:
        comment = self._pull(owner, repo, number).create_issue_comment(body)
        return comment.html_url

    def add_labels(self, owner: str, repo: str, number: int, labels: list[str]) -> None:
        if not self._gh:
            raise RuntimeError("GITHUB_TOKEN not set")
        self._gh.get_repo(f"{owner}/{repo}").get_issue(number).add_to_labels(*labels)
