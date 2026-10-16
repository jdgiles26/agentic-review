"""FastAPI application – callable HTTP API for the review agents."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from api.jobs import enqueue, get_job, list_jobs, start_queue, stop_queue
from graphs.review_graph import run_review
from memory.store import get_review
from schemas.models import (
    GitHubWebhookPayload,
    HealthResponse,
    ReviewRequest,
    ReviewResult,
)
from tools.github.client import GitHubClient

VERSION = "0.2.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await start_queue()
    yield
    await stop_queue()


app = FastAPI(
    title="Agentic Review Platform",
    description=(
        "Multi-agent code & PR review API. "
        "Supports direct diff review, GitHub PR review, and background webhook jobs."
    ),
    version=VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse)
async def health():
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
    )


@app.post("/v1/review", response_model=ReviewResult)
async def create_review(req: ReviewRequest):
    try:
        return run_review(req)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/v1/reviews/{request_id}")
async def read_review(request_id: str):
    saved = get_review(request_id)
    if not saved:
        raise HTTPException(status_code=404, detail="Review not found")
    return saved


@app.post("/v1/review/github/{owner}/{repo}/{number}", response_model=ReviewResult)
async def review_github_pr(owner: str, repo: str, number: int, focus: list[str] | None = None):
    client = GitHubClient()
    if not client.available:
        raise HTTPException(status_code=503, detail="GITHUB_TOKEN not configured")

    try:
        pr = client.get_pr(owner, repo, number)
        diff = client.get_pr_diff(owner, repo, number)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"GitHub error: {e}") from e

    req = ReviewRequest(
        repo_url=f"https://github.com/{owner}/{repo}",
        pr_number=number,
        diff=diff,
        files=pr.get("files"),
        focus=focus,  # type: ignore[arg-type]
    )
    return run_review(req)


@app.post("/v1/webhooks/github")
async def github_webhook(payload: GitHubWebhookPayload):
    if payload.action not in {"opened", "synchronize", "reopened"}:
        return {"status": "ignored", "reason": f"action={payload.action}"}

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
            {"owner": owner, "repo": repo, "number": number, "post_comment": True},
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    return {"status": "queued", "job_id": job.id, "pr": number, "repo": full_name}


@app.get("/v1/jobs/{job_id}")
async def read_job(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "error": job.error,
        "result": job.result,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
    }


@app.get("/v1/jobs")
async def read_jobs():
    return [
        {"id": j.id, "kind": j.kind, "status": j.status, "created_at": j.created_at}
        for j in list_jobs()
    ]


@app.get("/")
async def root():
    return {
        "name": "Agentic Review Platform",
        "docs": "/docs",
        "health": "/health",
        "review": "POST /v1/review",
        "github_pr": "POST /v1/review/github/{owner}/{repo}/{number}",
        "webhook": "POST /v1/webhooks/github",
        "jobs": "GET /v1/jobs/{job_id}",
    }
