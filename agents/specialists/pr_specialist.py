"""PR specialist – evaluates PR metadata and drafts review actions."""

from __future__ import annotations

import logging

from agents.base import ROLE_PROMPTS, UNTRUSTED_CONTENT_NOTE, fence, get_llm, llm_available
from schemas.models import AgentRole, PRAction
from tools.tracing import record_token_usage, span

log = logging.getLogger(__name__)

XL_CHANGE_LINES = 800
MIN_DESCRIPTION_CHARS = 40


def recommend_actions(body: str | None, additions: int, deletions: int) -> list[PRAction]:
    """Deterministic PR hygiene checks. These always run, with or without a model."""
    actions: list[PRAction] = []
    if additions + deletions > XL_CHANGE_LINES:
        actions.append(
            PRAction(
                action="label",
                labels=["size/XL"],
                body="Large change – consider splitting if possible.",
            )
        )
    if not body or len(body.strip()) < MIN_DESCRIPTION_CHARS:
        actions.append(
            PRAction(
                action="comment",
                body="Please expand the PR description with motivation, test plan, and risk notes.",
            )
        )
    return actions


def evaluate_pr(
    title: str,
    body: str | None,
    changed_files: list[str],
    additions: int,
    deletions: int,
    model: str | None = None,
) -> tuple[str, list[PRAction]]:
    """Return a summary and recommended GitHub actions.

    The summary is empty when no model is available; the graph then writes its own.
    """
    actions = recommend_actions(body, additions, deletions)
    if not llm_available():
        return "", actions

    prompt = (
        f"{ROLE_PROMPTS[AgentRole.PR_SPECIALIST]}\n\n"
        "Write a short review summary (3-6 sentences) for this pull request.\n"
        f"{UNTRUSTED_CONTENT_NOTE}\n"
        f"Title:\n{fence(title)}\n"
        f"Body:\n{fence(body or '(empty)')}\n"
        f"Files changed: {len(changed_files)}\n"
        f"+{additions} -{deletions}\n"
        f"Sample files: {changed_files[:15]}"
    )
    with span("llm.pr_summary", **{"agent.role": AgentRole.PR_SPECIALIST.value}) as current:
        try:
            response = get_llm(model).invoke(prompt)
        except Exception as exc:  # noqa: BLE001 - summary is best effort
            log.warning("PR specialist failed: %s", exc)
            return "", actions
        record_token_usage(current, getattr(response, "usage_metadata", None))
    content = response.content if hasattr(response, "content") else response
    return (content if isinstance(content, str) else str(content)).strip(), actions
