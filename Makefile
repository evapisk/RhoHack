.PHONY: install backend frontend test graph-demo demo-inject demo-inject-normal

## One-time setup for both halves
install:
	cd backend && uv sync
	cd backend && test -f .env || cp .env.example .env
	cd frontend && pnpm install

## Run the API + poller + SSE server on :8000 (reads backend/.env)
backend:
	cd backend && uv run uvicorn app.main:app --reload --port 8000

## Run the Vite dev server on :5173 (proxies /api to :8000)
frontend:
	cd frontend && pnpm dev

test:
	cd backend && uv run pytest -q

## Build the graph from the sandbox fixture and print stats (no server needed)
graph-demo:
	cd backend && uv run python -m app.graph.demo

## Push a suspicious transaction through the live pipeline ($250,000 to a never-seen vendor)
demo-inject:
	curl -s -X POST localhost:8000/api/demo/inject \
	  -H 'content-type: application/json' \
	  -d '{"counterparty_name":"Totally New Vendor LLC","amount":-25000000,"account_id":"30000000-0000-4000-8000-000000000006","memo":"Consulting retainer"}' \
	  | python3 -m json.tool

## Push a boring transaction through the live pipeline (should score normal)
demo-inject-normal:
	curl -s -X POST localhost:8000/api/demo/inject \
	  -H 'content-type: application/json' \
	  -d '{"counterparty_name":"Northstar Office Supply","amount":-18450,"account_id":"30000000-0000-4000-8000-000000000002"}' \
	  | python3 -m json.tool
