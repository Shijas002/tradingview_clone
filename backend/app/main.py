"""
FastAPI application factory.

Startup order (lifespan):
  1. logging        -> console + logs/app.log
  2. ProviderRegistry  -> builds providers from ENABLED_PROVIDERS
  3. StreamManager     -> installs tick/status callbacks on every provider
  4. connect_all()     -> connects providers (a failing MT5 never blocks startup)
Shutdown runs the same steps in reverse.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api import alerts as alerts_api
from app.api import broker, journal, notify, paper, persistence, rest, websocket
from app.core.config import BASE_DIR, Settings, get_settings
from app.core.exceptions import DataBridgeError
from app.core.logging_config import setup_logging
from app.db.database import Database
from app.providers.registry import ProviderRegistry
from app.services.alert_engine import AlertEngine
from app.services.notion_journal import NotionJournal
from app.services.history_service import HistoryService
from app.services.notifier import Notifier
from app.services.pine_runtime import PineRuntime
from app.services.indicator_alerts import IndicatorAlertEngine
from app.services.paper_broker import PaperBroker
from app.services.journal_import import JournalImporter
from app.services.stream_manager import StreamManager

log = logging.getLogger("app")


class NoCacheStaticFiles(StaticFiles):
    """Static files that browsers must revalidate, so a rebuilt app is picked up immediately."""

    async def get_response(self, path, scope):  # type: ignore[override]
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response

VIEWER_FILE = Path(__file__).resolve().parent / "static" / "viewer.html"
# Built React app (Phase 2): tradingview-clone/frontend/dist
FRONTEND_DIST = BASE_DIR.parent / "frontend" / "dist"


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.log_level, settings.log_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        log.info("Starting %s (env=%s)", settings.app_name, settings.app_env)
        db = Database(settings.database_url_resolved)
        db.create_all()
        registry = ProviderRegistry(settings)
        manager = StreamManager(registry, queue_size=settings.ws_client_queue_size)
        history = HistoryService(db, registry, settings)
        notifier = Notifier(settings)
        alert_engine = AlertEngine(db, registry, manager.broadcast, notifier)

        async def fetch_bars(provider_name: str, symbol: str, timeframe, count: int):
            prov = registry.get(provider_name)
            try:
                canonical = prov.normalize_symbol(symbol)
            except DataBridgeError:
                if ":" not in symbol:
                    raise
                canonical = prov.normalize_symbol(symbol.split(":", 1)[1])
            return await history.get(prov, canonical, timeframe, count)

        pine_runtime = PineRuntime(fetch_bars)
        indicator_alerts = IndicatorAlertEngine(db, registry, history, pine_runtime, manager.broadcast, notifier, settings)
        notion = NotionJournal(settings, db, manager.broadcast)
        paper_broker = PaperBroker(db, registry, manager.broadcast, notifier, settings, notion)
        journal_import = JournalImporter(db, registry, manager.broadcast, notifier, settings, notion)
        app.state.settings = settings
        app.state.db = db
        app.state.registry = registry
        app.state.stream_manager = manager
        app.state.history = history
        app.state.notifier = notifier
        app.state.pine_runtime = pine_runtime
        app.state.indicator_alerts = indicator_alerts
        app.state.alert_engine = alert_engine
        app.state.notion = notion
        app.state.paper = paper_broker
        app.state.journal_import = journal_import
        await registry.connect_all()
        await alert_engine.start()
        await indicator_alerts.start()
        await notion.start()
        await paper_broker.start()
        await journal_import.start()
        log.info("Ready on http://%s:%s  (docs: /docs, stream: /ws/market)", settings.app_host, settings.app_port)
        try:
            yield
        finally:
            log.info("Shutting down...")
            await journal_import.stop()
            await paper_broker.stop()
            await notion.stop()
            await indicator_alerts.stop()
            await alert_engine.stop()
            await pine_runtime.close()
            await notifier.close()
            await manager.close()
            await registry.disconnect_all()
            db.dispose()
            log.info("Shutdown complete")

    app = FastAPI(
        title=settings.app_name,
        version=rest.APP_VERSION,
        description="TradingView Clone backend: market data (MT5, OANDA, Upstox), persistence, alerts, journal, paper trading.",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(DataBridgeError)
    async def _bridge_error_handler(request: Request, exc: DataBridgeError) -> JSONResponse:
        log.warning("%s %s -> %s: %s", request.method, request.url.path, exc.status_code, exc.message)
        return JSONResponse(status_code=exc.status_code, content={"error": exc.message})

    @app.exception_handler(Exception)
    async def _unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"error": "Internal server error - see logs/app.log"})

    app.include_router(rest.router)
    app.include_router(persistence.router)
    app.include_router(alerts_api.router)
    app.include_router(broker.router)
    app.include_router(journal.router)
    app.include_router(notify.router)
    app.include_router(paper.router)
    app.include_router(websocket.router)

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        target = "/app/" if (FRONTEND_DIST / "index.html").exists() else "/viewer"
        return RedirectResponse(url=target)

    if (FRONTEND_DIST / "index.html").exists():
        app.mount("/app", NoCacheStaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
    else:

        @app.get("/app", include_in_schema=False)
        @app.get("/app/{_rest:path}", include_in_schema=False)
        async def app_missing(_rest: str = "") -> HTMLResponse:
            return HTMLResponse(
                "<h2>Chart app not built</h2><p>The folder <code>frontend/dist</code> is missing. "
                "Run <code>npm install</code> and <code>npm run build</code> inside <code>frontend</code> "
                "(see TUTORIAL.md), or use the simple viewer at <a href='/viewer'>/viewer</a>.</p>",
                status_code=503,
            )

    @app.get("/viewer", include_in_schema=False)
    async def viewer() -> FileResponse:
        """Phase 1 live viewer: a single-file chart page that uses this backend's REST + WebSocket."""
        if not VIEWER_FILE.exists():
            raise DataBridgeError(f"Viewer file missing: {VIEWER_FILE}")
        return FileResponse(VIEWER_FILE, media_type="text/html", headers={"Cache-Control": "no-cache"})

    return app


app = create_app()
