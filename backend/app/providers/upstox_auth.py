"""
Upstox authentication: which access token to use, and the OAuth login flow.

Two ways to authenticate (see TUTORIAL.md, "Upstox"):

* **Analytics Token** (recommended for charting) - a read-only token valid for
  one year, generated in the Upstox developer console and pasted into
  ``UPSTOX_ANALYTICS_TOKEN``. It can read historical candles and the live market
  data WebSocket, and it cannot place orders. No daily login.
* **OAuth login** - ``UPSTOX_API_KEY`` + ``UPSTOX_API_SECRET`` of your Upstox app.
  The app sends you to Upstox's login page, Upstox redirects back to
  ``UPSTOX_REDIRECT_URI`` (``/api/broker/upstox/callback``) with a one-time code,
  and the backend exchanges it for an access token. Upstox access tokens expire
  at **03:30 IST the next morning**, so this login is needed once per day.

The OAuth token is stored in ``backend/data/upstox_token.json``. Only what is
needed is kept: the token, its expiry and your Upstox client id. The profile
fields Upstox returns with the token (name, e-mail, ...) are deliberately
discarded and never logged.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import httpx

from app.core.config import Settings
from app.core.exceptions import InvalidRequestError, ProviderUnavailableError

log = logging.getLogger("app.providers.upstox.auth")

API_BASE = "https://api.upstox.com"
AUTH_DIALOG_URL = f"{API_BASE}/v2/login/authorization/dialog"
TOKEN_PATH = "/v2/login/authorization/token"
IST = timezone(timedelta(hours=5, minutes=30))
STATE_TTL_S = 600


def next_token_expiry(issued_at: datetime) -> datetime:
    """Upstox tokens expire at 03:30 IST on the day after they are issued (or the same day if issued before 03:30)."""
    local = issued_at.astimezone(IST)
    expiry = local.replace(hour=3, minute=30, second=0, microsecond=0)
    if local >= expiry:
        expiry += timedelta(days=1)
    return expiry.astimezone(timezone.utc)


@dataclass
class StoredToken:
    access_token: str
    expires_at: float  # UNIX seconds
    user_id: str = ""
    obtained_at: float = 0.0

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at - 30


class UpstoxAuth:
    """Holds the active Upstox token and runs the OAuth code exchange."""

    def __init__(self, settings: Settings, transport: Optional[httpx.AsyncBaseTransport] = None) -> None:
        self._settings = settings
        self._transport = transport
        self._file: Path = settings.data_path / "upstox_token.json"
        self._states: Dict[str, float] = {}
        self._stored: Optional[StoredToken] = self._load()
        self._rejected: Optional[str] = None  # token the API refused (don't retry it)

    # ------------------------------------------------------------ storage
    def _load(self) -> Optional[StoredToken]:
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
            token = StoredToken(
                access_token=str(data["access_token"]),
                expires_at=float(data["expires_at"]),
                user_id=str(data.get("user_id", "")),
                obtained_at=float(data.get("obtained_at", 0)),
            )
        except FileNotFoundError:
            return None
        except (ValueError, KeyError, TypeError) as exc:
            log.warning("Ignoring unreadable %s (%s)", self._file.name, exc)
            return None
        if token.expired:
            log.info("Stored Upstox login expired at %s - log in again", _fmt(token.expires_at))
            return None
        log.info("Loaded Upstox login (valid until %s)", _fmt(token.expires_at))
        return token

    def _save(self, token: StoredToken) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._file.with_suffix(".tmp")
        tmp.write_text(json.dumps(token.__dict__, indent=2), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)  # best effort; ignored on Windows
        except OSError:
            pass
        tmp.replace(self._file)

    def clear(self) -> None:
        """Forget the OAuth login (the Analytics Token in .env is not touched)."""
        self._stored = None
        self._rejected = None
        try:
            self._file.unlink()
        except FileNotFoundError:
            pass
        log.info("Upstox login cleared")

    # -------------------------------------------------------------- state
    @property
    def mode(self) -> str:
        """'analytics' when UPSTOX_ANALYTICS_TOKEN is set, else 'oauth' when the API key/secret are set, else 'none'."""
        if self._settings.upstox_analytics_token:
            return "analytics"
        if self._settings.upstox_oauth_configured:
            return "oauth"
        return "none"

    def active_token(self) -> Optional[str]:
        """The token to send to Upstox right now, or None when nobody is logged in."""
        token: Optional[str] = None
        if self._settings.upstox_analytics_token:
            token = self._settings.upstox_analytics_token
        elif self._stored is not None:
            if self._stored.expired:
                log.info("Upstox login expired (tokens end at 03:30 IST) - log in again")
                self._stored = None
            else:
                token = self._stored.access_token
        if token is not None and token == self._rejected:
            return None
        return token

    def mark_rejected(self, token: str) -> None:
        """Upstox answered 401 for this token - stop using it until a new one arrives."""
        self._rejected = token
        if self._stored is not None and self._stored.access_token == token:
            self._stored = None
            try:
                self._file.unlink()
            except FileNotFoundError:
                pass

    def expires_at(self) -> Optional[float]:
        if self._settings.upstox_analytics_token:
            return None  # one year; Upstox does not expose the date through the API
        return self._stored.expires_at if self._stored is not None else None

    def describe(self) -> Dict[str, Any]:
        token = self.active_token()
        if self.mode == "none":
            hint = "Add UPSTOX_ANALYTICS_TOKEN (or UPSTOX_API_KEY + UPSTOX_API_SECRET) to backend/.env and restart"
        elif self.mode == "analytics":
            hint = (
                "Using the Analytics Token from .env"
                if token
                else "Upstox rejected UPSTOX_ANALYTICS_TOKEN - generate a new one and restart"
            )
        else:
            hint = "Logged in" if token else "Click 'Log in to Upstox' (needed once a day; tokens end at 03:30 IST)"
        return {
            "broker": "upstox",
            "mode": self.mode,
            "authenticated": token is not None,
            "user_id": (self._stored.user_id if self._stored and self.mode == "oauth" else None) or None,
            "expires_at": self.expires_at(),
            "login_available": self.mode == "oauth",
            "redirect_uri": self._settings.upstox_redirect_uri if self.mode == "oauth" else None,
            "message": hint,
        }

    # -------------------------------------------------------------- OAuth
    def login_url(self) -> str:
        if not self._settings.upstox_oauth_configured:
            raise InvalidRequestError(
                "Upstox login is not configured: set UPSTOX_API_KEY, UPSTOX_API_SECRET and "
                "UPSTOX_REDIRECT_URI in backend/.env and restart the backend"
            )
        now = time.time()
        self._states = {s: t for s, t in self._states.items() if now - t < STATE_TTL_S}
        state = secrets.token_urlsafe(24)
        self._states[state] = now
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self._settings.upstox_api_key,
                "redirect_uri": self._settings.upstox_redirect_uri,
                "state": state,
            }
        )
        return f"{AUTH_DIALOG_URL}?{query}"

    def _consume_state(self, state: Optional[str]) -> None:
        issued = self._states.pop(state or "", None)
        if issued is None or time.time() - issued > STATE_TTL_S:
            raise InvalidRequestError(
                "Login link expired or was not started from this app. Start again from the app's 'Log in to Upstox' button."
            )

    async def exchange_code(self, code: str, state: Optional[str]) -> StoredToken:
        """Swap the one-time ``code`` from the callback for an access token and store it."""
        self._consume_state(state)
        if not code:
            raise InvalidRequestError("Upstox did not send an authorization code")
        form = {
            "code": code,
            "client_id": self._settings.upstox_api_key,
            "client_secret": self._settings.upstox_api_secret,
            "redirect_uri": self._settings.upstox_redirect_uri,
            "grant_type": "authorization_code",
        }
        try:
            async with httpx.AsyncClient(
                base_url=API_BASE, timeout=self._settings.upstox_request_timeout_s, transport=self._transport
            ) as client:
                resp = await client.post(TOKEN_PATH, data=form, headers={"Accept": "application/json"})
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(f"Could not reach Upstox to finish the login: {exc}") from exc
        if resp.status_code != 200:
            raise InvalidRequestError(f"Upstox refused the login: {error_text(resp)}")
        try:
            body = resp.json()
        except ValueError as exc:
            raise ProviderUnavailableError("Upstox returned an unreadable token response") from exc
        access_token = str(body.get("access_token") or "")
        if not access_token:
            raise InvalidRequestError(f"Upstox token response had no access_token: {error_text(resp)}")
        now = datetime.now(timezone.utc)
        token = StoredToken(
            access_token=access_token,
            expires_at=next_token_expiry(now).timestamp(),
            user_id=str(body.get("user_id") or ""),
            obtained_at=now.timestamp(),
        )
        self._stored = token
        self._rejected = None
        self._save(token)
        log.info("Upstox login successful (valid until %s)", _fmt(token.expires_at))
        return token


def error_text(resp: httpx.Response) -> str:
    """Best human-readable message from an Upstox error response."""
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:300] or f"HTTP {resp.status_code}"
    errors = body.get("errors") if isinstance(body, dict) else None
    if isinstance(errors, list) and errors:
        first = errors[0] or {}
        msg = first.get("message") or first.get("errorCode")
        code = first.get("errorCode") or first.get("error_code")
        if msg:
            return f"{msg} ({code})" if code and code != msg else str(msg)
    if isinstance(body, dict) and body.get("message"):
        return str(body["message"])
    return f"HTTP {resp.status_code}"


def _fmt(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=IST).strftime("%Y-%m-%d %H:%M IST")
