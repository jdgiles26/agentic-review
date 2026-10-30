# Review console UI

Next.js app for `agentic-review`.

## Capabilities

- Dark console layout with live `/health` polling (shows whether an LLM and GitHub are configured).
- Paste a unified diff or review a GitHub PR.
- Findings show severity, category, confidence, and `file:line`; filter by severity.
- Shows the agents that ran (as chips), PR hygiene actions, and a warning when a review is partial.
- GitHub mode explains the endpoint and whether the API has `GITHUB_TOKEN` configured.
- Accessible tab semantics and a responsive layout down to phone width.
- Sample diff to cut first-run friction.

## Run

```bash
cd ui
npm ci
NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev
```
