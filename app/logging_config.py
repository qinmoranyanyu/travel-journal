from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterable


LOG_FILENAME = "travel-journal.log"
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 5
_HANDLER_MARKER = "_travel_journal_handler"


class RedactingFormatter(logging.Formatter):
    def __init__(self, fmt: str, datefmt: str, secrets: Iterable[str] = ()) -> None:
        super().__init__(fmt=fmt, datefmt=datefmt)
        self.secrets = tuple(secret for secret in secrets if len(secret) >= 6)

    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        for secret in self.secrets:
            rendered = rendered.replace(secret, "[REDACTED]")
        return rendered


def configure_logging(
    log_dir: Path,
    level: str = "INFO",
    secrets: Iterable[str] = (),
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
) -> Path:
    shutdown_logging()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / LOG_FILENAME
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    formatter = RedactingFormatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        secrets=secrets,
    )

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(numeric_level)
    file_handler.setFormatter(formatter)
    setattr(file_handler, _HANDLER_MARKER, True)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(formatter)
    setattr(console_handler, _HANDLER_MARKER, True)

    root_logger = logging.getLogger()
    root_logger.setLevel(min(root_logger.level or numeric_level, numeric_level))
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    # Uvicorn owns its loggers and disables propagation, so attach only the
    # rotating file handler to keep server errors in the same diagnostic file.
    for name in ("uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).addHandler(file_handler)

    return log_file


def shutdown_logging() -> None:
    loggers = [
        logging.getLogger(),
        logging.getLogger("uvicorn.error"),
        logging.getLogger("uvicorn.access"),
    ]
    handlers: set[logging.Handler] = set()
    for logger in loggers:
        for handler in list(logger.handlers):
            if getattr(handler, _HANDLER_MARKER, False):
                logger.removeHandler(handler)
                handlers.add(handler)
    for handler in handlers:
        handler.flush()
        handler.close()
