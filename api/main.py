"""FastAPI application – callable HTTP API for the review agents."""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from agents.base import llm_available
from api.jobs import enqueue, get_job, list_jobs, posting_enabled, start_queue, stop_queue
from graphs.review_graph import run_review
from memory.store import get_review, list_reviews
from schemas.models import (
    GitHubWebhookPayload,
    HealthResponse,
    JobView,
    ReviewCategory,
    ReviewRequest,
    ReviewResult,
    WebhookAccepted,
)
from tools.github.client import GitHubClient
from tools.github.pr_context import build_pr_review_request

log = logging.getLogger(__name__)

VERSION = "0.2.0"
REVIEW_ACTIONS = {"opened", "synchronize", "reopened", "ready_for_review"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    await start_queue()
    yield
    await stop_queue()


app = FastAPI(
    title="Agentic Review Platform",
    description=(
        "Multi-agent code & PR review API. Supports direct diff review, GitHub PR review, and background webhook jobs."
    ),
    version=VERSION,
    lifespan=lifespan,
)

_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    # Credentials with a wildcard origin would let any site make authenticated calls.
    allow_credentials="*" not in _origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

try:  # Spans are exported only when the host configures an OTel SDK.
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app)
except Exception:  # noqa: BLE001 - observability is optional
    log.debug("FastAPI OpenTelemetry instrumentation unavailable", exc_info=True)


def _truthy(name: str) -> bool:
    return os.getenv(name, "").lower() in {"1", "true", "yes"}


@app.get("/health", response_model=HealthResponse)
def health():
    return HealthResponse(
        status="ok",
        version=VERSION,
        agents_ready=[
            "supervisor",
            "code_reviewer",
            "security_specialist",
            "architecture_reviewer",
            "test_specialist",
            "style_linter",
            "pr_specialist",
        ],
        llm_configured=llm_available(),
        github_configured=GitHubClient().available,
    )


@app.post("/v1/review", response_model=ReviewResult)
def create_review(req: ReviewRequest):
    """Run a full multi-agent review on a diff (plus optional PR context)."""
    try:
        return run_review(req)
    except Exception as e:
        log.exception("Review failed")
        raise HTTPException(status_code=500, detail=f"Review failed: {e}") from e


@app.get("/v1/reviews")
def read_reviews(limit: int = Query(20, ge=1, le=200)) -> list[dict[str, Any]]:
    return list_reviews(limit=limit)


@app.get("/v1/reviews/{request_id}", response_model=ReviewResult)
def read_review(request_id: str):
    saved = get_review(request_id)
    if not saved:
        raise HTTPException(status_code=404, detail="Review not found")
    return saved


@app.post("/v1/review/github/{owner}/{repo}/{number}", response_model=ReviewResult)
def review_github_pr(
    owner: str,
    repo: str,
    number: int,
    focus: list[ReviewCategory] | None = Query(None),
    model: str | None = Query(None),
):
    """Fetch a PR from GitHub and run the review pipeline."""
    client = GitHubClient()
    if not client.available:
        raise HTTPException(status_code=503, detail="GITHUB_TOKEN not configured")
    try:
        req = build_pr_review_request(client, owner, repo, number, focus=focus, model=model)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"GitHub error: {e}") from e
    return create_review(req)


def _verify_signature(body: bytes, signature: str | None) -> None:
    secret = os.getenv("GITHUB_WEBHOOK_SECRET")
    if not secret:
        if _truthy("ALLOW_UNSIGNED_WEBHOOKS"):
            return
        raise HTTPException(
            status_code=503,
            detail="GITHUB_WEBHOOK_SECRET not configured (set ALLOW_UNSIGNED_WEBHOOKS=true for local testing)",
        )
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not signature or not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")


@app.post("/v1/webhooks/github", status_code=202, response_model=WebhookAccepted)
async def github_webhook(
    request: Request,
    x_github_event: str | None = Header(None),
    x_hub_signature_256: str | None = Header(None),
):
    """Receive GitHub pull_request events and queue a background review."""
    body = await request.body()
    _verify_signature(body, x_hub_signature_256)

    if x_github_event == "ping":
        return _ok({"status": "pong"})
    if x_github_event not in {None, "pull_request"}:
        return _ok({"status": "ignored", "reason": f"event={x_github_event}"})

    try:
        payload = GitHubWebhookPayload.model_validate_json(body or b"{}")
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=f"Invalid payload: {e}") from e

    if payload.action not in REVIEW_ACTIONS:
        return _ok({"status": "ignored", "reason": f"action={payload.action}"})
    if not payload.pull_request or not payload.repository:
        raise HTTPException(status_code=400, detail="Missing PR or repository")

    full_name = payload.repository.get("full_name") or ""
    if "/" not in full_name:
        raise HTTPException(status_code=400, detail="Repository full_name missing")
    owner, repo = full_name.split("/", 1)
    number = payload.number or payload.pull_request.get("number")
    if not number:
        raise HTTPException(status_code=400, detail="PR number missing")

    try:
        job = enqueue(
            "github_pr_review",
            {"owner": owner, "repo": repo, "number": number, "post_comment": posting_enabled()},
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return WebhookAccepted(job_id=job.id, pr=number, repo=full_name)


def _ok(content: dict[str, Any]) -> JSONResponse:
    """Non-queued webhook replies are 200, not the route's default 202."""
    return JSONResponse(content, status_code=200)


@app.get("/v1/jobs/{job_id}", response_model=JobView)
def read_job(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.view()


@app.get("/v1/jobs", response_model=list[JobView])
def read_jobs(limit: int = Query(20, ge=1, le=200)):
    return [j.view() for j in list_jobs(limit)]


@app.get("/")
def root():
    return {
        "name": "Agentic Review Platform",
        "docs": "/docs",
        "health": "/health",
        "review": "POST /v1/review",
        "reviews": "GET /v1/reviews/{request_id}",
        "github_pr": "POST /v1/review/github/{owner}/{repo}/{number}",
        "webhook": "POST /v1/webhooks/github",
        "jobs": "GET /v1/jobs/{job_id}",
    }
