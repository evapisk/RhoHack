from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

from .config import settings
from .feed import EventBroadcaster
from .graph import TransactionGraph
from .models import ScoredTransaction, Transaction
from .poller import TransactionPoller
from .rho_client import MockRhoClient, RhoClient
from .scoring import AnomalyScorer, ZScoreScorer

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="RhoHack anomaly feed")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten before demo if it matters
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- pipeline wiring ---------------------------------------------------
# poller -> graph.add_transaction -> scorer.score -> broadcaster.publish -> SSE
# This is the hour 7-9 merge point. Swap ZScoreScorer for AutoencoderScorer
# here (and only here) once it's ready.
graph = TransactionGraph()
scorer: AnomalyScorer = ZScoreScorer()
broadcaster = EventBroadcaster()

rho_client: RhoClient = MockRhoClient() if settings.use_mock_rho else RhoClient()


async def handle_transactions(transactions: list[Transaction]) -> None:
    for tx in transactions:
        graph.add_transaction(tx)
        score = scorer.score(tx, graph)
        await broadcaster.publish(ScoredTransaction(tx, score))


poller = TransactionPoller(
    client=rho_client,
    on_transactions=handle_transactions,
    interval_seconds=settings.poll_interval_seconds,
)


@app.on_event("startup")
async def startup() -> None:
    poller.start()


@app.on_event("shutdown")
async def shutdown() -> None:
    await poller.stop()
    await rho_client.aclose()


# --- routes --------------------------------------------------------------
@app.get("/health")
async def health() -> dict:
    return {"status": "ok", **graph.summary()}


@app.get("/feed")
async def feed() -> EventSourceResponse:
    return EventSourceResponse(broadcaster.sse_stream())


@app.post("/poll-now")
async def poll_now() -> dict:
    """Demo hook: force an immediate poll instead of waiting for the next
    interval tick (e.g. right after sending a test transaction live)."""
    poller.trigger_now()
    return {"triggered": True}
