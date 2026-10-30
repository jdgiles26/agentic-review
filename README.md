# Agentic Review Platform

Multi-agent AI system for **code review**, **PR specialist review**, **GitHub automation**, and **model/code quality review**.

Exposes:

| Surface | How to use |
|---------|------------|
| **Website / Dashboard** | Next.js UI at `ui/` – paste a diff *or* review a live GitHub PR |
| **HTTP API** | FastAPI – `POST /v1/review`, GitHub PR endpoint, webhooks, jobs, review history |
| **CLI** | `review diff file.patch`, `review pr owner repo number`, `review history` |
| **GitHub automation** | Signed webhook receiver → background review job → optional PR comment |

## How a review runs

```
supervisor ──┬─> code_reviewer ─────────┐
             ├─> security_specialist ───┤
             ├─> architecture_reviewer ─┼─> synthesize ─> result (+ saved to SQLite)
             ├─> test_specialist ───────┤
             ├─> style_linter ──────────┤
             └─> pr_specialist ─────────┘
```

- The **supervisor** picks specialists from `focus` (empty focus = all of them).
- Specialists run **in parallel** (one LangGraph superstep). Each returns Pydantic-validated findings.
- With `OPENAI_API_KEY` set, specialists use structured LLM output (primary model, then `REVIEW_FALLBACK_MODEL`).
  Without a key they run deterministic heuristics over the **added lines** of the diff, with `file:line` locations
  (plain source code that is not a diff is scanned in full).
- **synthesize** deduplicates, ranks by severity/confidence, caps at `max_findings`, and scores the change.
- A crashing specialist doesn't sink the review: the result comes back with `status: "partial"`.
- Every result keeps the agent trajectory in `raw_messages` and is stored in `REVIEW_DB` for replay.
  `run_review(request, thread_id=...)` also checkpoints the final state per thread (`memory.store.load_checkpoint`),
  and `build_review_graph(checkpointer=MemorySaver())` gives a graph with inspectable per-thread LangGraph state.
- `agents.reviewers.code_reviewer.analyze_diff` is a LangChain tool (Pydantic input schema) that returns
  structured findings as JSON, for use by other agents.
- Graph nodes, LLM calls (with token usage), jobs, and HTTP requests emit OpenTelemetry spans; configure an OTel SDK/exporter to collect them.

## Agents

| Agent | Responsibility |
|-------|----------------|
| Supervisor | Plans which specialists to run from `focus`, synthesizes the final recommendation |
| Code Reviewer | Correctness, edge cases, maintainability (also runs the security checks) |
| Security Specialist | Injection, secrets, auth flaws, insecure defaults |
| PR Specialist | Title/body quality, size, labels, review comment |
| Architecture Reviewer | Coupling, boundaries, large cross-cutting changes |
| Test Specialist | Coverage gaps, new logic without tests |
| Style Linter | Conventions, naming, TODO/FIXME, docs |

Built with **LangGraph**, **Pydantic**, **FastAPI**, **Typer**, and **Next.js**.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
review serve            # http://127.0.0.1:8000/docs
```

Website:

```bash
cd ui && npm ci
NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev
```

The console polls `/health`, reviews pasted diffs or GitHub PRs, and filters findings by severity.

CLI:

```bash
git diff | review diff - --focus security --fail-on-changes   # exit 2 on request_changes
review pr octo-org widgets 42 --json
review history
```

## API

```bash
curl http://localhost:8000/health

curl -X POST http://localhost:8000/v1/review \
  -H 'Content-Type: application/json' \
  -d '{"diff": "--- a/foo.py\n+++ b/foo.py\n@@ -0,0 +1 @@\n+result = eval(user_input)\n"}'

# Live GitHub PR (needs GITHUB_TOKEN on the API)
curl -X POST 'http://localhost:8000/v1/review/github/owner/repo/123?focus=security'
```

| Method | Path | Notes |
|--------|------|-------|
| GET | `/health` | Includes `llm_configured` / `github_configured` |
| POST | `/v1/review` | Body: `ReviewRequest` (`diff`, `focus`, `max_findings`, optional `title`/`body`/`additions`/`deletions`) |
| POST | `/v1/review/github/{owner}/{repo}/{number}?focus=security` | Needs `GITHUB_TOKEN`; focus may also be a JSON body list |
| GET | `/v1/reviews`, `/v1/reviews/{request_id}` | Stored review history |
| POST | `/v1/webhooks/github` | GitHub `pull_request` events; returns `{"status": "queued", "job_id": ...}` |
| GET | `/v1/jobs`, `/v1/jobs/{job_id}` | Background job status and result |

### GitHub automation setup

1. Create a GitHub App or token with `pull_requests: write` and `contents: read`, and set `GITHUB_TOKEN`.
2. Pick a webhook secret and set `GITHUB_WEBHOOK_SECRET` to it.
3. Add a repository webhook pointing at `POST /v1/webhooks/github` (content type `application/json`,
   same secret, events: `pull_request`).
4. On `opened` / `synchronize` / `reopened` / `ready_for_review` the API queues a review job
   (`GET /v1/jobs/{job_id}`); set `REVIEW_POST_COMMENTS=true` to post the result as a PR comment.

### Webhooks and safety defaults

- Webhooks are verified with `X-Hub-Signature-256` against `GITHUB_WEBHOOK_SECRET`.
  With no secret configured they are refused, unless `ALLOW_UNSIGNED_WEBHOOKS=true` (local testing only).
- Posting a review comment back to GitHub is opt-in: set `REVIEW_POST_COMMENTS=true`.
- `CORS_ORIGINS=*` disables credentialed CORS. List explicit origins in production.
- The job queue is in-process and in-memory, bounded by `REVIEW_MAX_JOBS` (default 500); jobs are lost on restart.

## Development

```bash
ruff check . && ruff format --check .
pytest --cov
cd ui && npm run build
```

## Docker

```bash
docker compose up --build    # review history persists in the review-data volume
# or
docker build -t agentic-review . && docker run -p 8000:8000 --env-file .env agentic-review
```

## Project layout

```
agents/     specialist agents (code reviewer, security, architecture, tests, style, PR)
graphs/     LangGraph orchestration (supervisor → parallel specialists → synthesize)
tools/      GitHub client, diff parsing, OpenTelemetry helpers
schemas/    Pydantic models (API contract)
api/        FastAPI app and background job queue
cli/        Typer CLI (`review`)
memory/     SQLite review history and checkpoints
ui/         Next.js review console
tests/      offline pytest suite
```

## License

MIT
