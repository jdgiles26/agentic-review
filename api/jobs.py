"""In-process background job queue for webhook-triggered reviews."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from graphs.review_graph import run_review
from schemas.models import ReviewRequest
from tools.github.client import GitHubClient


@dataclass
class Job:
    id: str
    kind: str
    payload: dict[str, Any]
    status: str = "queued"
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


_jobs: dict[str, Job] = {}
_queue: asyncio.Queue[str] | None = None
_worker: asyncio.Task | None = None


def get_job(job_id: str) -> Job | None:
    return _jobs.get(job_id)


def list_jobs(limit: int = 20) -> list[Job]:
    return sorted(_jobs.values(), key=lambda j: j.created_at, reverse=True)[:limit]


def enqueue(kind: str, payload: dict[str, Any]) -> Job:
    if _queue is None:
        raise RuntimeError("Job queue is not started")
    job = Job(id=str(uuid.uuid4()), kind=kind, payload=payload)
    _jobs[job.id] = job
    _queue.put_nowait(job.id)
    return job


def _execute(job: Job) -> dict[str, Any]:
    if job.kind != "github_pr_review":
        raise ValueError(f"Unknown job kind: {job.kind}")
    owner = job.payload["owner"]
    repo = job.payload["repo"]
    number = int(job.payload["number"])
    client = GitHubClient()
    if not client.available:
        raise RuntimeError("GITHUB_TOKEN not configured")
    pr = client.get_pr(owner, repo, number)
    diff = client.get_pr_diff(owner, repo, number)
    req = ReviewRequest(
        repo_url=f"https://github.com/{owner}/{repo}",
        pr_number=number,
        diff=diff,
        files=pr.get("files"),
    )
    result = run_review(req)
    if job.payload.get("post_comment") and result.summary:
        client.post_comment(owner, repo, number, result.summary)
    return result.model_dump(mode="json")


async def _worker_loop() -> None:
    assert _queue is not None
    while True:
        job_id = await _queue.get()
        job = _jobs.get(job_id)
        if not job:
            _queue.task_done()
            continue
        job.status = "running"
        job.started_at = time.time()
        try:
            job.result = await asyncio.to_thread(_execute, job)
            job.status = "completed"
        except Exception as exc:  # noqa: BLE001
            job.status = "failed"
            job.error = str(exc)
        finally:
            job.finished_at = time.time()
            _queue.task_done()


async def start_queue() -> None:
    global _queue, _worker
    if _queue is not None:
        return
    _queue = asyncio.Queue()
    _worker = asyncio.create_task(_worker_loop())


async def stop_queue() -> None:
    global _worker, _queue
    if _worker:
        _worker.cancel()
        _worker = None
    _queue = None
