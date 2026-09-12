"""FastAPI app: HTTP/SSE surface + lifecycle for the poller.

    cd backend && uv run uvicorn app.main:app --reload --port 8000

Routes
    GET  /api/health              liveness + poller/cursor status
    GET  /api/stream              SSE feed of ScoredTransaction (event: transaction)
    GET  /api/transactions        recent scored transactions, newest first (?limit=&flagged=true)
    GET  /api/graph               node/edge lists for visualization
    GET  /api/stats               pipeline counters + graph stats
    POST /api/poll-now            self-trigger hook: poll Rho now instead of waiting for the next tick
    POST /api/demo/inject         push a hand-made transaction through the pipeline (demo);
                                  ?dry_run=true scores it without publishing or touching the graph
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app.bus import TOPIC_TRANSACTIONS_NEW, TOPIC_TRANSACTIONS_SCORED, EventBus
from app.config import Settings, get_settings
from app.demo.scenarios import get_scenario, list_scenarios, run_scenario
from app.demo.seed import bootstrap, seed_from_fixture
from app.graph.graph import TransactionGraph
from app.models import ScoredTransaction, TransactionEvent
from app.pipeline import Pipeline
from app.poller.cursor import CursorState
from app.poller.poller import Poller
from app.poller.simulator import InjectRequest, ReplaySource, make_injected_transaction
from app.realtime.broadcaster import SSEBroadcaster
from app.rho_client import RhoClient, RhoError
from app.scoring import build_scorer

log = logging.getLogger("app")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    bus = EventBus()
    graph = TransactionGraph()
    scorer = build_scorer(settings)
    broadcaster = SSEBroadcaster()
    pipeline = Pipeline(graph, scorer, bus)
    bus.subscribe(TOPIC_TRANSACTIONS_SCORED, broadcaster.publish)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        client = RhoClient(settings.rho_base_url, settings.rho_api_key)
        app.state.demo_lock = asyncio.Lock()

        # Live Rho when it answers, the checked-in fixture when it does not. A missing
        # key or a dead network degrades the demo instead of aborting startup.
        source, accounts = await bootstrap(
            graph=graph, pipeline=pipeline, bus=bus, client=client, settings=settings
        )
        app.state.data_source = source
        app.state.accounts = accounts

        tasks: list[asyncio.Task] = []
        app.state.poller = None
        if source == "rho":
            cursor_path = settings.state_dir / "cursor.json"
            poller = Poller(client, bus, CursorState.load(cursor_path), settings, cursor_path)
            app.state.poller = poller
            tasks.append(asyncio.create_task(poller.run(), name="poller"))
        else:
            # No poller in fixture mode: one that fails every 5s only pollutes the log.
            await seed_from_fixture(
                graph=graph, pipeline=pipeline, bus=bus,
                fixtures_dir=settings.fixtures_dir, accounts=accounts, source="fixture",
            )

        app.state.replay = None
        if settings.demo_replay:
            replay = ReplaySource(bus, settings.fixtures_dir, settings.demo_replay_interval_seconds)
            app.state.replay = replay
            tasks.append(asyncio.create_task(replay.run(), name="replay"))
        app.state.tasks = tasks

        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await client.aclose()

    app = FastAPI(title="Rho Transaction Graph", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.settings = settings
    app.state.bus = bus
    app.state.graph = graph
    app.state.pipeline = pipeline
    app.state.broadcaster = broadcaster

    # -- routes ---------------------------------------------------------------

    @app.get("/api/health")
    async def health(request: Request) -> dict[str, Any]:
        poller: Poller | None = getattr(request.app.state, "poller", None)
        tasks: list[asyncio.Task] = getattr(request.app.state, "tasks", [])
        task_state = {
            t.get_name(): ("running" if not t.done() else f"stopped: {t.exception()!r}" if not t.cancelled() and t.exception() else "stopped")
            for t in tasks
        }
        source = getattr(request.app.state, "data_source", "rho")
        return {
            "status": "ok",
            "scorer": scorer.name,
            "data_source": source,
            "offline": source == "fixture",
            "rho_base_url": settings.rho_base_url,
            "sse_clients": broadcaster.client_count,
            "poller": poller.status() if poller else None,
            "tasks": task_state,
        }

    @app.get("/api/stream")
    async def stream(request: Request) -> StreamingResponse:
        return StreamingResponse(
            broadcaster.stream(request),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
        )

    @app.get("/api/transactions", response_model=list[ScoredTransaction])
    async def transactions(
        limit: int = Query(default=100, ge=1, le=1000),
        flagged: bool = Query(default=False, description="Only warn/alert"),
    ) -> list[ScoredTransaction]:
        return pipeline.recent(limit, flagged_only=flagged)

    @app.get("/api/graph")
    async def graph_view() -> dict[str, Any]:
        return graph.to_node_link()

    @app.get("/api/stats")
    async def stats() -> dict[str, Any]:
        return {
            "pipeline": pipeline.counters,
            "graph": graph.stats(),
            "sse": {"clients": broadcaster.client_count, "sent": broadcaster.sent, "dropped": broadcaster.dropped},
            "bus": dict(bus.published),
        }

    @app.post("/api/poll-now")
    async def poll_now(request: Request) -> dict[str, Any]:
        """Self-trigger hook: poll Rho immediately instead of waiting for the next interval tick."""
        poller: Poller | None = getattr(request.app.state, "poller", None)
        if poller is None:
            raise HTTPException(status_code=503, detail="poller not running (fixture mode)")
        poller.trigger_now()
        return {"triggered": True}

    @app.get("/api/demo/scenarios")
    async def demo_scenarios() -> dict[str, Any]:
        return {"scenarios": list_scenarios()}

    @app.post("/api/demo/scenarios/{scenario_id}")
    async def demo_run_scenario(scenario_id: str, request: Request) -> dict[str, Any]:
        scenario = get_scenario(scenario_id)
        if scenario is None:
            raise HTTPException(status_code=404, detail=f"unknown scenario {scenario_id!r}")
        async with request.app.state.demo_lock:
            return await run_scenario(
                scenario,
                graph=graph,
                pipeline=pipeline,
                bus=bus,
                broadcaster=broadcaster,
                fixtures_dir=settings.fixtures_dir,
                accounts=getattr(request.app.state, "accounts", []),
            )

    @app.post("/api/demo/reset")
    async def demo_reset(request: Request) -> dict[str, Any]:
        """Rebuild graph and history from the fixture without restarting the server."""
        async with request.app.state.demo_lock:
            result = await seed_from_fixture(
                graph=graph,
                pipeline=pipeline,
                bus=bus,
                fixtures_dir=settings.fixtures_dir,
                accounts=getattr(request.app.state, "accounts", None),
                broadcaster=broadcaster,
            )
        return {"reset": True, **result.as_dict(), "graph": graph.stats(), "counters": pipeline.counters}

    @app.post("/api/demo/inject", response_model=ScoredTransaction)
    async def inject(
        req: InjectRequest,
        request: Request,
        dry_run: bool = Query(default=False, description="Score only; do not publish or mutate the graph"),
    ) -> ScoredTransaction:
        tx = make_injected_transaction(req, getattr(request.app.state, "accounts", []))
        event = TransactionEvent(kind="new", transaction=tx, backfill=False, source="inject")
        if dry_run:
            features = graph.features_for(tx)
            return ScoredTransaction(
                event=event, features=features, anomaly=scorer.score(tx, features), scored_at=datetime.now(timezone.utc)
            )
        await bus.publish(TOPIC_TRANSACTIONS_NEW, event)
        scored = pipeline.last
        if scored is None or scored.event.transaction.id != tx.id:
            raise HTTPException(status_code=500, detail="pipeline did not score the injected transaction")
        return scored

    return app


app = create_app()
