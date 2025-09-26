"""Shared logging configuration utilities."""

from __future__ import annotations

import json
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional


LOG_DIR_ENV = "ONEDRIVE_LOG_DIR"
LOG_LEVEL_ENV = "ONEDRIVE_LOG_LEVEL"
DEFAULT_LOG_DIR = Path("~/.local/share/onedrive-linux-sync/logs").expanduser()
DEFAULT_LOG_FILE = "onedrive-sync.log"
MAX_BYTES = 5 * 1024 * 1024  # 5 MiB
BACKUP_COUNT = 5

_CONFIGURED = False


class JsonFormatter(logging.Formatter):
    """Serialize log records as JSON objects."""

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        payload = {
            "time": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "name": record.name,
            "module": record.module,
            "line": record.lineno,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: Optional[str] = None, log_dir: Optional[Path] = None) -> logging.Logger:
    """Configure application logging with rotating file handler.

    When invoked multiple times the first call wins; subsequent calls only adjust
    the root logger level.
    """

    global _CONFIGURED

    resolved_level = (level or os.getenv(LOG_LEVEL_ENV, "INFO")).upper()
    log_level = getattr(logging, resolved_level, logging.INFO)

    resolved_dir = log_dir or Path(os.getenv(LOG_DIR_ENV, DEFAULT_LOG_DIR)).expanduser()
    resolved_dir.mkdir(parents=True, exist_ok=True)
    log_path = resolved_dir / DEFAULT_LOG_FILE

    root_logger = logging.getLogger()

    if _CONFIGURED:
        root_logger.setLevel(log_level)
        return root_logger

    formatter = JsonFormatter()

    file_handler = RotatingFileHandler(log_path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT)
    file_handler.setFormatter(formatter)

    root_logger.handlers.clear()
    root_logger.setLevel(log_level)
    root_logger.addHandler(file_handler)

    _CONFIGURED = True
    return root_logger


__all__ = ["setup_logging", "JsonFormatter", "LOG_DIR_ENV", "LOG_LEVEL_ENV"]

