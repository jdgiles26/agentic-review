"""In-process background job queue for webhook-triggered reviews.

Jobs live in memory and are lost on restart. The pending queue holds at most
MAX_JOBS jobs (further webhooks get 503) and finished jobs are pruned, so a busy
webhook cannot grow memory without limit. Posting results back to GitHub is an
outward-facing action and is gated behind REVIEW_POST_COMMENTS=true.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Literal

from graphs.review_graph import run_review
from schemas.models import JobView, ReviewCategory
from tools.github.client import GitHubClient
from tools.github.pr_context import build_pr_review_request, format_review_comment
from tools.tracing import span

log = logging.getLogger(__name__)

MAX_JOBS = 500
JobStatus = Literal["queued", "running", "completed", "failed"]


@dataclass
class Job:
    id: str
    kind: str
    payload: dict[str, Any]
    status: JobStatus = "queued"
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    result: dict[str, Any] | None = None
    comment_url: str | None = None
    error: str | None = None

    def view(self) -> JobView:
        return JobView(
            id=self.id,
            kind=self.kind,
            status=self.status,
            error=self.error,
            result=self.result,
            comment_url=self.comment_url,
            created_at=self.created_at,
            started_at=self.started_at,
            finished_at=self.finished_at,
        )


_jobs: OrderedDict[str, Job] = OrderedDict()
_queue: asyncio.Queue[str] | None = None
_worker: asyncio.Task | None = None


def posting_enabled() -> bool:
    return os.getenv("REVIEW_POST_COMMENTS", "").lower() in {"1", "true", "yes"}


def get_job(job_id: str) -> Job | None:
    return _jobs.get(job_id)


def list_jobs(limit: int = 20) -> list[Job]:
    return sorted(_jobs.values(), key=lambda j: j.created_at, reverse=True)[:limit]


def _register(job: Job) -> None:
    _jobs[job.id] = job
    if len(_jobs) <= MAX_JOBS:
        return
    # Drop the oldest finished jobs first; never drop work that is still pending.
    for job_id in [j for j, v in _jobs.items() if v.status in {"completed", "failed"}]:
        if len(_jobs) <= MAX_JOBS:
            break
        del _jobs[job_id]


def reset() -> None:
    _jobs.clear()


def enqueue(kind: str, payload: dict[str, Any]) -> Job:
    if _queue is None:
        raise RuntimeError("Job queue is not started")
    job = Job(id=str(uuid.uuid4()), kind=kind, payload=payload)
    try:
        _queue.put_nowait(job.id)
    except asyncio.QueueFull as exc:
        raise RuntimeError("Job queue is full, try again later") from exc
    _register(job)
    return job


def _execute(job: Job) -> None:
    if job.kind != "github_pr_review":
        raise ValueError(f"Unknown job kind: {job.kind}")
    owner = job.payload["owner"]
    repo = job.payload["repo"]
    number = int(job.payload["number"])
    client = GitHubClient()
    if not client.available:
        raise RuntimeError("GITHUB_TOKEN not configured")
    focus = [ReviewCategory(c) for c in job.payload.get("focus") or []]
    result = run_review(build_pr_review_request(client, owner, repo, number, focus=focus))
    job.result = result.model_dump(mode="json")
    if job.payload.get("post_comment"):
        job.comment_url = client.post_comment(owner, repo, number, format_review_comment(result))


async def _worker_loop() -> None:
    assert _queue is not None
    queue = _queue
    while True:
        job_id = await queue.get()
        job = _jobs.get(job_id)
        if not job:
            queue.task_done()
            continue
        job.status = "running"
        job.started_at = time.time()
        try:
            with span("review.job", **{"job.id": job.id, "job.kind": job.kind}):
                await asyncio.to_thread(_execute, job)
            job.status = "completed"
        except Exception as exc:  # noqa: BLE001 - recorded on the job for the caller
            log.exception("Job %s failed", job.id)
            job.status = "failed"
            job.error = str(exc)
        finally:
            job.finished_at = time.time()
            queue.task_done()


async def start_queue() -> None:
    global _queue, _worker
    if _queue is not None:
        return
    _queue = asyncio.Queue(maxsize=MAX_JOBS)
    _worker = asyncio.create_task(_worker_loop())


async def stop_queue() -> None:
    global _worker, _queue
    if _worker:
        _worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _worker
        _worker = None
    _queue = None
