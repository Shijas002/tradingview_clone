"""
Start the backend:  python run.py

Host, port, log level and auto-reload come from backend/.env.
"""

from __future__ import annotations

import sys

import uvicorn

from app.core.config import get_settings


def main() -> int:
    try:
        settings = get_settings()
        _ = settings.enabled_provider_list  # validate early with a clear message
        _ = settings.oanda_symbol_overrides
    except Exception as exc:
        print(f"[config error] {exc}\nCheck your backend/.env file.", file=sys.stderr)
        return 1

    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.uvicorn_reload,
        log_config=None,  # logging is configured by the app itself
        ws_ping_interval=20,
        ws_ping_timeout=20,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
