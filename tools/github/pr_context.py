"""Turn a GitHub PR into a ReviewRequest, and a ReviewResult into a PR comment."""

from __future__ import annotations

from typing import Protocol

from schemas.models import ReviewCategory, ReviewRequest, ReviewResult

COMMENT_MAX_FINDINGS = 15


class PRSource(Protocol):
    def get_pr_context(self, owner: str, repo: str, number: int) -> dict: ...


def build_pr_review_request(
    client: PRSource,
    owner: str,
    repo: str,
    number: int,
    focus: list[ReviewCategory] | None = None,
    model: str | None = None,
) -> ReviewRequest:
    pr = client.get_pr_context(owner, repo, number)
    return ReviewRequest(
        repo_url=f"https://github.com/{owner}/{repo}",
        pr_number=number,
        commit_sha=pr.get("head_sha"),
        diff=pr.get("diff") or "",
        files=pr.get("files"),
        focus=focus or None,
        model=model,
        title=pr.get("title"),
        body=pr.get("body"),
        additions=pr.get("additions") or 0,
        deletions=pr.get("deletions") or 0,
    )


def format_review_comment(result: ReviewResult) -> str:
    """Markdown comment summarising a review for a GitHub PR."""
    lines = [
        "## Agentic review",
        "",
        f"**Recommendation:** `{result.recommendation}` · **Score:** {result.score}/10",
        "",
        result.summary,
    ]
    if result.findings:
        lines += ["", "| Severity | Category | Finding | Location |", "|---|---|---|---|"]
        for f in result.findings[:COMMENT_MAX_FINDINGS]:
            where = (
                f"`{f.file_path}:{f.line_start}`"
                if f.file_path and f.line_start
                else (f"`{f.file_path}`" if f.file_path else "—")
            )
            title = f.title.replace("|", "\\|")
            lines.append(f"| {f.severity.value} | {f.category.value} | {title} | {where} |")
        if len(result.findings) > COMMENT_MAX_FINDINGS:
            lines.append(f"\n_{len(result.findings) - COMMENT_MAX_FINDINGS} more findings omitted._")
    for action in result.actions:
        if action.body:
            lines += ["", f"> {action.body}"]
    lines += ["", f"<sub>review id `{result.request_id}`</sub>"]
    return "\n".join(lines)
