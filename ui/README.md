# Review console UI

Next.js app for `agentic-review`.

## Capabilities

- Dark console layout with live `/health` polling.
- Paste a unified diff or review a GitHub PR.
- Findings show severity, confidence, and optional cost.
- Sample diff to cut first-run friction.

## Run

```bash
cd ui
npm install
NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev
```
