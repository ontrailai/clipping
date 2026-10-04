"""Logging with a name tag per team member, so the daily log reads like a crew talking."""

from __future__ import annotations

import logging
import sys

ROLES = {
    "producer": "🎬 Producer",
    "scout": "🔭 Scout",
    "analyst": "🧠 Analyst",
    "editor": "✂️  Editor",
    "copywriter": "✍️  Copywriter",
    "qc": "🛡️  QC",
    "publisher": "📤 Publisher",
}

_configured = False


def setup(level: int = logging.INFO, logfile: str | None = None) -> None:
    global _configured
    if _configured:
        return
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if logfile:
        handlers.append(logging.FileHandler(logfile, encoding="utf-8"))
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )
    for noisy in ("httpx", "httpx2", "urllib3", "googleapiclient", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True


def get(role: str) -> logging.Logger:
    return logging.getLogger(ROLES.get(role, role))
