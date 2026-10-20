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
  Without a key they run deterministic heuristics over the **added lines** of the diff, with `file:line` locations.
- **synthesize** deduplicates, ranks by severity/confidence, caps at `max_findings`, and scores the change.
- A crashing specialist doesn't sink the review: the result comes back with `status: "partial"`.
- Every result keeps the agent trajectory in `raw_messages` and is stored in `REVIEW_DB` for replay.
- Graph nodes, LLM calls (with token usage), jobs, and HTTP requests emit OpenTelemetry spans; configure an OTel SDK/exporter to collect them.

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

| Method | Path | Notes |
|--------|------|-------|
| GET | `/health` | Includes `llm_configured` / `github_configured` |
| POST | `/v1/review` | Body: `ReviewRequest` (`diff`, `focus`, `max_findings`, optional `title`/`body`/`additions`/`deletions`) |
| POST | `/v1/review/github/{owner}/{repo}/{number}?focus=security` | Needs `GITHUB_TOKEN` |
| GET | `/v1/reviews`, `/v1/reviews/{request_id}` | Stored review history |
| POST | `/v1/webhooks/github` | GitHub `pull_request` events; returns 202 + `job_id` |
| GET | `/v1/jobs`, `/v1/jobs/{job_id}` | Background job status and result |

### Webhooks and safety defaults

- Webhooks are verified with `X-Hub-Signature-256` against `GITHUB_WEBHOOK_SECRET`.
  With no secret configured they are refused, unless `ALLOW_UNSIGNED_WEBHOOKS=true` (local testing only).
- Posting a review comment back to GitHub is opt-in: set `REVIEW_POST_COMMENTS=true`.
- `CORS_ORIGINS=*` disables credentialed CORS. List explicit origins in production.
- The job queue is in-process and in-memory (bounded to 500 jobs); jobs are lost on restart.

## Development

```bash
ruff check . && ruff format --check .
pytest --cov
cd ui && npm run build
```

## Docker

```bash
docker compose up --build    # review history persists in the review-data volume
```

## License

MIT
