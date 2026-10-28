"""
Broker login endpoints (prefix /api/broker).

GET  /api/broker/status             - login state of the Indian broker (Upstox)
GET  /api/broker/upstox/login       - redirect the browser to Upstox's login page
GET  /api/broker/upstox/callback    - Upstox redirects here with ?code=...&state=...
POST /api/broker/upstox/logout      - forget the stored daily login

The callback URL must be registered *exactly* as the Redirect URL of your app in
the Upstox developer console (default http://127.0.0.1:8000/api/broker/upstox/callback).
"""

from __future__ import annotations

import html
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.exceptions import DataBridgeError, ProviderNotFoundError
from app.providers.upstox_provider import UpstoxProvider

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/broker", tags=["broker"])


def _upstox(request: Request) -> UpstoxProvider:
    try:
        provider = request.app.state.registry.get("upstox")
    except ProviderNotFoundError as exc:
        raise ProviderNotFoundError(
            "The Upstox provider is not enabled. Add 'upstox' to ENABLED_PROVIDERS in backend/.env and restart."
        ) from exc
    assert isinstance(provider, UpstoxProvider)
    return provider


@router.get("/status")
async def broker_status(request: Request) -> Dict[str, Any]:
    try:
        provider = _upstox(request)
    except ProviderNotFoundError:
        return {"broker": "upstox", "enabled": False, "mode": "none", "authenticated": False,
                "login_available": False, "message": "Add 'upstox' to ENABLED_PROVIDERS in backend/.env"}
    info = provider.auth.describe()
    info["enabled"] = True
    info["provider"] = provider.status().model_dump(mode="json")
    return info


@router.get("/upstox/login")
async def upstox_login(request: Request) -> Any:
    provider = _upstox(request)
    try:
        url = provider.auth.login_url()
    except DataBridgeError as exc:
        return _page("Upstox login not configured", exc.message, ok=False, status=400)
    log.info("Redirecting browser to the Upstox login page")
    return RedirectResponse(url, status_code=302)


@router.get("/upstox/callback")
async def upstox_callback(
    request: Request,
    code: Optional[str] = Query(default=None, max_length=512),
    state: Optional[str] = Query(default=None, max_length=256),
    error: Optional[str] = Query(default=None, max_length=256),
    error_description: Optional[str] = Query(default=None, max_length=512),
) -> HTMLResponse:
    provider = _upstox(request)
    if error:
        return _page("Upstox login cancelled", error_description or error, ok=False, status=400)
    try:
        await provider.auth.exchange_code(code or "", state)
    except DataBridgeError as exc:
        log.warning("Upstox login failed: %s", exc.message)
        return _page("Upstox login failed", exc.message, ok=False, status=exc.status_code)
    provider.on_auth_changed()
    return _page("Upstox connected", "You are logged in. Live Indian market data is starting - returning to the chart…", ok=True)


@router.post("/upstox/logout")
async def upstox_logout(request: Request) -> Dict[str, Any]:
    provider = _upstox(request)
    provider.auth.clear()
    provider.on_auth_changed()
    return provider.auth.describe()


def _page(title: str, message: str, ok: bool, status: int = 200) -> HTMLResponse:
    color = "#26a69a" if ok else "#ef5350"
    redirect = '<meta http-equiv="refresh" content="2;url=/app/">' if ok else ""
    body = f"""<!doctype html><html><head><meta charset="utf-8">{redirect}<title>{html.escape(title)}</title>
<style>body{{background:#131722;color:#d1d4dc;font:15px system-ui,sans-serif;display:flex;align-items:center;
justify-content:center;height:100vh;margin:0}}.card{{background:#1e222d;border:1px solid #2a2e39;border-radius:8px;
padding:28px 32px;max-width:520px}}h1{{color:{color};font-size:20px;margin:0 0 12px}}a{{color:#2962ff}}</style></head>
<body><div class="card"><h1>{html.escape(title)}</h1><p>{html.escape(message)}</p>
<p><a href="/app/">Back to the chart</a></p></div>
<script>try{{if(window.opener){{window.opener.postMessage({{type:"broker-login",ok:{str(ok).lower()}}},location.origin);{'setTimeout(()=>window.close(),1500);' if ok else ''}}}}}catch(e){{}}</script>
</body></html>"""
    return HTMLResponse(body, status_code=status)
