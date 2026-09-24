# Agentic Review Platform

Multi-agent AI system for **code review**, **PR specialist review**, **GitHub automation**, and **model/code quality review**.

Exposes:

| Surface | How to use |
|---------|------------|
| **Website / Dashboard** | Next.js UI at `ui/` – paste a diff *or* review a live GitHub PR |
| **HTTP API** | FastAPI – `POST /v1/review`, GitHub PR endpoint, webhooks |
| **CLI** | `review diff file.patch` or `review pr owner repo number` |
| **GitHub automation** | Webhook receiver + optional comment/label actions |

## Quick start

```bash
cd agentic-review
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
review serve
```

Website:

```bash
cd ui && npm install
NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev
```

The console polls `/health`, reviews pasted diffs or GitHub PRs, and filters findings by severity.

## License

MIT
