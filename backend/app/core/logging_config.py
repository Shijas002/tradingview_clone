"""
Central logging setup: coloured-free console output plus a rotating log file
at ``backend/logs/app.log`` (path controlled by LOG_DIR).
"""

from __future__ import annotations

import logging
import logging.config
from pathlib import Path

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(level: str, log_dir: Path) -> None:
    """Configure root, application and uvicorn loggers."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "app.log"

    config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "standard": {"format": LOG_FORMAT, "datefmt": DATE_FORMAT},
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "standard",
                "level": level,
            },
            "file": {
                "class": "logging.handlers.RotatingFileHandler",
                "formatter": "standard",
                "level": level,
                "filename": str(log_file),
                "maxBytes": 5 * 1024 * 1024,
                "backupCount": 5,
                "encoding": "utf-8",
            },
        },
        "root": {"handlers": ["console", "file"], "level": level},
        "loggers": {
            "uvicorn": {"handlers": ["console", "file"], "level": level, "propagate": False},
            "uvicorn.error": {"handlers": ["console", "file"], "level": level, "propagate": False},
            "uvicorn.access": {"handlers": ["console", "file"], "level": "WARNING", "propagate": False},
            "websockets": {"level": "WARNING"},
            "asyncio": {"level": "WARNING"},
        },
    }
    logging.config.dictConfig(config)
    logging.getLogger(__name__).debug("Logging initialised (level=%s, file=%s)", level, log_file)
