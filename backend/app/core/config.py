"""
Application configuration.

All settings are read from environment variables and from ``backend/.env``.
The ``.env`` file is always resolved relative to the ``backend`` folder, so the
server behaves the same no matter which directory you launch it from.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> parents[2] == backend/
BASE_DIR: Path = Path(__file__).resolve().parents[2]
ENV_FILE: Path = BASE_DIR / ".env"

SUPPORTED_PROVIDERS = {"simulated", "mt5", "oanda", "upstox", "auto"}
UPSTOX_EXCHANGES = ("NSE", "BSE", "MCX")


class Settings(BaseSettings):
    """Typed view over the environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # keys for future phases may already live in .env
    )

    # ------------------------------------------------------------------ app
    app_name: str = "TradingView Clone - Data Bridge"
    app_env: str = Field(default="development", description="development | production")
    app_host: str = "127.0.0.1"
    app_port: int = Field(default=8000, ge=1, le=65535)
    uvicorn_reload: bool = False
    log_level: str = "INFO"
    log_dir: str = "logs"
    # Runtime files (MT5 offset, Upstox login + instrument cache). Relative = inside backend/
    data_dir: str = "data"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # ------------------------------------------------------------ providers
    enabled_providers: str = "simulated,mt5,oanda,upstox,auto"
    default_provider: str = "auto"
    history_max_bars: int = Field(default=5000, ge=10, le=100_000)
    history_default_bars: int = Field(default=500, ge=1, le=100_000)

    # ------------------------------------------------------------------ MT5
    mt5_terminal_path: str = ""
    mt5_login: str = ""
    mt5_password: str = ""
    mt5_server: str = ""
    mt5_timeout_ms: int = Field(default=60_000, ge=1_000)
    mt5_portable: bool = False
    mt5_poll_interval_ms: int = Field(default=100, ge=10, le=10_000)
    mt5_reconnect_max_delay_s: float = Field(default=30.0, ge=1.0)
    # Hours between MT5 server time and UTC (e.g. 2 or 3). Empty = auto-detect from live ticks.
    mt5_server_utc_offset_hours: str = ""

    # ---------------------------------------------------------------- OANDA
    oanda_api_token: str = ""
    oanda_account_id: str = ""
    oanda_environment: str = "practice"
    # Extra symbol mappings "MT5NAME=OANDA_NAME,..." e.g. "GER40=DE30_EUR,USTEC=NAS100_USD"
    oanda_symbol_map: str = ""
    oanda_request_timeout_s: float = Field(default=15.0, ge=1.0)
    oanda_reconnect_max_delay_s: float = Field(default=30.0, ge=1.0)

    # --------------------------------------------------------------- Upstox
    # Option A (easiest): a 1-year read-only Analytics Token from the Upstox developer console.
    upstox_analytics_token: str = ""
    # Option B: daily login through the app (API key + secret of your Upstox app).
    upstox_api_key: str = ""
    upstox_api_secret: str = ""
    upstox_redirect_uri: str = "http://127.0.0.1:8000/api/broker/upstox/callback"
    # Instrument lists to load (symbol search): any of NSE, BSE, MCX
    upstox_exchanges: str = "NSE,BSE,MCX"
    # Options contracts add ~100k symbols; off by default (futures, stocks, indices are always loaded)
    upstox_include_options: bool = False
    # "full" = price + best bid/ask + real volume, "ltpc" = price + last traded quantity only
    upstox_feed_mode: str = "full"
    upstox_request_timeout_s: float = Field(default=20.0, ge=1.0)
    upstox_reconnect_max_delay_s: float = Field(default=60.0, ge=1.0)

    # ------------------------------------------------------------- failover
    failover_primary: str = "mt5"
    failover_fallback: str = "oanda"
    # Seconds the primary must stay healthy before switching back to it
    failover_switch_back_delay_s: float = Field(default=15.0, ge=0.0)

    # ------------------------------------------------------------ simulator
    sim_tick_interval_ms: int = Field(default=250, ge=10, le=60_000)

    # ------------------------------------------------------------ websocket
    ws_client_queue_size: int = Field(default=2000, ge=10)

    # ------------------------------------------------------------- database
    # SQLite file for layouts, drawings, scripts, alerts (and the journal in Phase 5).
    database_url: str = "sqlite:///./data/tradingview.db"

    # --------------------------------------------------------------- alerts
    # Optional: every triggered alert is also POSTed as JSON to this URL (Discord/Slack/n8n/...)
    alert_webhook_url: str = ""

    # ------------------------------------------------------- Notion journal
    # Internal integration secret (starts with "ntn_") from notion.so/profile/integrations
    notion_api_key: str = ""
    # Database (or data source) ID - or the database's full URL - shared with the integration
    notion_database_id: str = ""
    # Alternative: a page shared with the integration; the journal database is created there for you
    notion_parent_page_id: str = ""
    notion_request_timeout_s: float = Field(default=30.0, ge=1.0)

    # ---------------------------------------------------------- validators
    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        value = value.upper().strip()
        if value not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
            raise ValueError(f"Invalid LOG_LEVEL '{value}'")
        return value

    @field_validator("mt5_terminal_path")
    @classmethod
    def _validate_terminal_path(cls, value: str) -> str:
        value = value.strip().strip("'")
        if any(ord(ch) < 32 for ch in value):
            raise ValueError(
                "MT5_TERMINAL_PATH contains a control character. Remove the double quotes "
                "around the path in .env (inside double quotes '\\t' becomes a TAB)."
            )
        return value

    @field_validator("oanda_environment")
    @classmethod
    def _validate_oanda_env(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"practice", "live"}:
            raise ValueError("OANDA_ENVIRONMENT must be 'practice' or 'live'")
        return value

    @field_validator("upstox_feed_mode")
    @classmethod
    def _validate_upstox_mode(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"full", "ltpc"}:
            raise ValueError("UPSTOX_FEED_MODE must be 'full' or 'ltpc'")
        return value

    @field_validator("upstox_exchanges")
    @classmethod
    def _validate_upstox_exchanges(cls, value: str) -> str:
        names = [x.strip().upper() for x in value.split(",") if x.strip()]
        bad = [x for x in names if x not in UPSTOX_EXCHANGES]
        if bad:
            raise ValueError(f"UPSTOX_EXCHANGES has unknown exchange(s) {bad}; use any of {', '.join(UPSTOX_EXCHANGES)}")
        if not names:
            raise ValueError("UPSTOX_EXCHANGES must list at least one of NSE, BSE, MCX")
        return ",".join(dict.fromkeys(names))

    @field_validator("notion_api_key", "notion_database_id", "notion_parent_page_id")
    @classmethod
    def _strip_notion(cls, value: str) -> str:
        value = value.strip().strip('"').strip("'")
        if any(ch.isspace() for ch in value):
            raise ValueError("Notion keys/IDs must be on one line without spaces")
        return value

    @field_validator("upstox_analytics_token", "upstox_api_key", "upstox_api_secret", "upstox_redirect_uri")
    @classmethod
    def _strip_upstox(cls, value: str) -> str:
        value = value.strip().strip('"').strip("'")
        if any(ch.isspace() for ch in value):
            raise ValueError("Upstox keys/tokens must not contain spaces or line breaks - paste the value on one line")
        return value

    @field_validator("mt5_server_utc_offset_hours")
    @classmethod
    def _validate_offset(cls, value: str) -> str:
        value = value.strip()
        if value:
            try:
                hours = float(value)
            except ValueError as exc:
                raise ValueError("MT5_SERVER_UTC_OFFSET_HOURS must be a number like 2 or 3 (or empty)") from exc
            if not -12 <= hours <= 14:
                raise ValueError("MT5_SERVER_UTC_OFFSET_HOURS must be between -12 and 14")
        return value

    @field_validator("mt5_login")
    @classmethod
    def _validate_mt5_login(cls, value: str) -> str:
        value = value.strip()
        if value and not value.isdigit():
            raise ValueError("MT5_LOGIN must be your numeric account number (or left empty)")
        return value

    # ------------------------------------------------------- derived values
    @property
    def cors_origin_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def enabled_provider_list(self) -> List[str]:
        names = [p.strip().lower() for p in self.enabled_providers.split(",") if p.strip()]
        unknown = [n for n in names if n not in SUPPORTED_PROVIDERS]
        if unknown:
            raise ValueError(
                f"Unknown provider(s) in ENABLED_PROVIDERS: {unknown}. "
                f"Supported: {sorted(SUPPORTED_PROVIDERS)}"
            )
        names = list(dict.fromkeys(names))  # keep order, drop duplicates
        if "auto" in names:
            if self.failover_primary == self.failover_fallback or "auto" in (self.failover_primary, self.failover_fallback):
                raise ValueError("FAILOVER_PRIMARY and FAILOVER_FALLBACK must be two different real providers")
            missing = [p for p in (self.failover_primary, self.failover_fallback) if p not in names]
            if missing:
                raise ValueError(
                    f"ENABLED_PROVIDERS includes 'auto' but not its sources {missing}. "
                    f"Add them, e.g. ENABLED_PROVIDERS=simulated,{self.failover_primary},{self.failover_fallback},auto"
                )
            names.remove("auto")
            names.append("auto")  # composite must be built and connected last
        return names

    @property
    def log_path(self) -> Path:
        path = Path(self.log_dir)
        return path if path.is_absolute() else BASE_DIR / path

    @property
    def mt5_server_offset_seconds(self) -> Optional[int]:
        """Configured MT5 server-time offset in seconds, or None for auto-detect."""
        if not self.mt5_server_utc_offset_hours:
            return None
        return int(round(float(self.mt5_server_utc_offset_hours) * 3600))

    @property
    def oanda_symbol_overrides(self) -> dict:
        """Parse OANDA_SYMBOL_MAP into {"GER40": "DE30_EUR", ...} (keys upper-cased)."""
        out = {}
        for pair in self.oanda_symbol_map.split(","):
            pair = pair.strip()
            if not pair:
                continue
            if "=" not in pair:
                raise ValueError(f"OANDA_SYMBOL_MAP entry '{pair}' must look like MT5NAME=OANDA_NAME")
            left, right = (x.strip() for x in pair.split("=", 1))
            if not left or not right:
                raise ValueError(f"OANDA_SYMBOL_MAP entry '{pair}' has an empty side")
            out[left.upper()] = right.upper()
        return out

    @property
    def data_path(self) -> Path:
        path = Path(self.data_dir)
        return path if path.is_absolute() else BASE_DIR / path

    @property
    def database_url_resolved(self) -> str:
        """Make relative SQLite paths relative to the backend folder (not the current directory)."""
        url = self.database_url.strip()
        prefix = "sqlite:///"
        if url.startswith(prefix) and not url.startswith("sqlite:////"):
            raw = url[len(prefix):]
            if raw and raw != ":memory:":
                path = Path(raw)
                if not path.is_absolute():
                    path = (BASE_DIR / path).resolve()
                path.parent.mkdir(parents=True, exist_ok=True)
                return f"{prefix}{path.as_posix()}"
        return url

    @property
    def notion_configured(self) -> bool:
        return bool(self.notion_api_key and (self.notion_database_id or self.notion_parent_page_id))

    @property
    def upstox_exchange_list(self) -> List[str]:
        return [x for x in self.upstox_exchanges.split(",") if x]

    @property
    def upstox_oauth_configured(self) -> bool:
        return bool(self.upstox_api_key and self.upstox_api_secret and self.upstox_redirect_uri)

    @property
    def mt5_credentials_configured(self) -> bool:
        return bool(self.mt5_login and self.mt5_password and self.mt5_server)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
