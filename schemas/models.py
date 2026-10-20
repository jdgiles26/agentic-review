"""Pydantic schemas for all agent inputs, outputs, and API contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class ReviewCategory(str, Enum):
    SECURITY = "security"
    PERFORMANCE = "performance"
    STYLE = "style"
    ARCHITECTURE = "architecture"
    CORRECTNESS = "correctness"
    TEST_COVERAGE = "test_coverage"
    DOCUMENTATION = "documentation"
    DEPENDENCIES = "dependencies"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Finding(BaseModel):
    id: str
    category: ReviewCategory
    severity: Severity
    title: str
    description: str
    file_path: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    suggestion: str | None = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.8)


class ReviewRequest(BaseModel):
    """Request to run a review (API / CLI / webhook)."""

    repo_url: str | None = None
    pr_number: int | None = None
    branch: str | None = None
    commit_sha: str | None = None
    diff: str | None = None
    files: list[str] | None = None
    focus: list[ReviewCategory] | None = None
    max_findings: int = Field(default=50, ge=1, le=200)
    include_suggestions: bool = True
    model: str | None = None  # override default model
    # Optional PR context, filled in for GitHub reviews and read by the PR specialist.
    title: str | None = None
    body: str | None = None
    additions: int = Field(default=0, ge=0)
    deletions: int = Field(default=0, ge=0)


class AgentRole(str, Enum):
    SUPERVISOR = "supervisor"
    CODE_REVIEWER = "code_reviewer"
    SECURITY_SPECIALIST = "security_specialist"
    PR_SPECIALIST = "pr_specialist"
    ARCHITECTURE_REVIEWER = "architecture_reviewer"
    TEST_SPECIALIST = "test_specialist"
    STYLE_LINTER = "style_linter"


class AgentMessage(BaseModel):
    role: AgentRole
    content: str
    findings: list[Finding] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=_utcnow)


class PRAction(BaseModel):
    """Action the PR specialist can take on GitHub."""

    action: Literal[
        "comment",
        "request_changes",
        "approve",
        "label",
        "assign",
        "request_reviewers",
    ]
    body: str | None = None
    labels: list[str] | None = None
    reviewers: list[str] | None = None


class ReviewResult(BaseModel):
    request_id: str
    status: Literal["completed", "partial", "failed", "needs_human"]
    summary: str
    findings: list[Finding]
    agents_involved: list[AgentRole]
    score: float | None = Field(default=None, ge=0.0, le=10.0)
    recommendation: Literal["approve", "request_changes", "comment"] | None = None
    actions: list[PRAction] = Field(default_factory=list)
    raw_messages: list[AgentMessage] = Field(default_factory=list)
    cost_usd: float | None = None
    duration_ms: int | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class GitHubWebhookPayload(BaseModel):
    action: str
    number: int | None = None
    pull_request: dict[str, Any] | None = None
    repository: dict[str, Any] | None = None
    sender: dict[str, Any] | None = None


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    agents_ready: list[str]
    llm_configured: bool = False
    github_configured: bool = False


class JobView(BaseModel):
    """Public view of a background review job."""

    id: str
    kind: str
    status: Literal["queued", "running", "completed", "failed"]
    error: str | None = None
    result: ReviewResult | None = None
    comment_url: str | None = None
    created_at: float
    started_at: float | None = None
    finished_at: float | None = None


class WebhookAccepted(BaseModel):
    status: Literal["queued"] = "queued"
    job_id: str
    pr: int
    repo: str
