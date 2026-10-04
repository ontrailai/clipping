"""Find the streamer's facecam box once per creator (Claude vision), then cache it."""

from __future__ import annotations

import json
import time
from pathlib import Path

from .. import log
from ..db import DB
from ..llm import Claude, LLMError, image_block, text_block

logger = log.get("editor")

SCHEMA = {
    "type": "object",
    "properties": {
        "has_facecam": {"type": "boolean"},
        "x": {"type": "number"},
        "y": {"type": "number"},
        "w": {"type": "number"},
        "h": {"type": "number"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
    },
}

SYSTEM = (
    "You locate webcam overlays in livestream screenshots. Reply with the bounding box of the "
    "streamer's facecam (the live camera of the person, not game characters or chat/alerts) as "
    "fractions of the image: x,y = top-left corner, w,h = size, all between 0 and 1. If the "
    "screenshots show no facecam, set has_facecam=false and zeros."
)

REDETECT_AFTER = 14 * 86400


def sane(box: dict | None) -> bool:
    if not box:
        return False
    try:
        x, y, w, h = (float(box[k]) for k in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        return False
    area = w * h
    return 0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 and 0 < h <= 1 and x + w <= 1.02 and y + h <= 1.02 and 0.01 <= area <= 0.45


def pad(box: dict, amount: float = 0.04) -> dict:
    x = max(0.0, box["x"] - amount * box["w"])
    y = max(0.0, box["y"] - amount * box["h"])
    w = min(1.0 - x, box["w"] * (1 + 2 * amount))
    h = min(1.0 - y, box["h"] * (1 + 2 * amount))
    return {"x": round(x, 4), "y": round(y, 4), "w": round(w, 4), "h": round(h, 4)}


def detect(claude: Claude, frames: list[Path]) -> dict | None:
    content = [image_block(f) for f in frames]
    content.append(text_block("Where is the streamer's facecam in these frames from the same stream?"))
    result = claude.json(SYSTEM, content, SCHEMA, effort="low", max_tokens=4000)
    if not result.get("has_facecam") or result.get("confidence") == "low":
        return None
    box = {k: result[k] for k in ("x", "y", "w", "h")}
    return pad(box) if sane(box) else None


def facecam_for(db: DB, claude: Claude | None, creator: str, platform: str, frames: list[Path], manual: dict | None) -> dict | None:
    if manual:
        return manual if sane(manual) else None
    key = f"facecam:{creator.lower()}:{platform}"
    cached = db.kv_get(key)
    if cached:
        data = json.loads(cached)
        if time.time() - data.get("at", 0) < REDETECT_AFTER:
            return data.get("box")
    if claude is None or not frames:
        return None
    try:
        box = detect(claude, frames)
    except LLMError as e:
        logger.warning("facecam detection failed: %s", e)
        return None
    db.kv_set(key, json.dumps({"box": box, "at": time.time()}))
    logger.info("facecam for %s on %s: %s", creator, platform, box or "none (using blur-fill layout)")
    return box
