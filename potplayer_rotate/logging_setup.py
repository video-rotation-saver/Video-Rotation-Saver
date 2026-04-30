"""File logging to the local app data log path, rotated at 1 MB."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from .paths import log_path

_CONFIGURED = False


def get_logger() -> logging.Logger:
    global _CONFIGURED
    lg = logging.getLogger("potplayer_rotate")
    if _CONFIGURED:
        return lg
    lg.setLevel(logging.INFO)
    handler = RotatingFileHandler(log_path(), maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-5s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    ))
    lg.addHandler(handler)
    _CONFIGURED = True
    return lg


def clear_logs() -> None:
    """Close active file handlers, clear current/rotated logs, and re-arm logging."""
    global _CONFIGURED
    lg = logging.getLogger("potplayer_rotate")
    for handler in list(lg.handlers):
        handler.close()
        lg.removeHandler(handler)
    _CONFIGURED = False

    current = log_path()
    candidates = [current, *(current.with_name(f"{current.name}.{i}") for i in range(1, 4))]
    for path in candidates:
        try:
            if path.exists():
                if path == current:
                    path.write_text("", encoding="utf-8")
                else:
                    path.unlink()
        except OSError:
            pass
