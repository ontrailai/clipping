"""Twitch chat replay → reaction timeline.

Uses TwitchDownloaderCLI when it's installed (fast, robust), otherwise Twitch's public
GraphQL comments endpoint with a time budget. Either way, failure just means no chat signal.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from collections import Counter
from pathlib import Path

import numpy as np

from .. import log
from ..http import session

logger = log.get("analyst")

GQL = "https://gql.twitch.tv/gql"
WEB_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
COMMENTS_HASH = "b70a3591ff0f4e0313d126c6a1502d79a1c02baebb288227c582044aa76adf6a"

FUNNY = {"lul", "lulw", "kekw", "omegalul", "icant", "lmao", "lmfao", "kekl", "pepelaugh", "xdd", "😂", "💀", "🤣", "haha", "hahaha", "dead", "lol"}
HYPE = {"pog", "pogchamp", "poggers", "pagman", "pogu", "holy", "w", "clip", "clipit", "noway", "wtf", "monkas", "monkaw", "😱", "🔥", "goat", "insane", "omg", "sheesh"}


def classify(text: str) -> tuple[float, str | None]:
    """Weight of one chat message and its reaction kind."""
    tokens = re.findall(r"[\w']+|[^\w\s]", text.lower())
    joined = text.lower().replace(" ", "")
    funny = any(t in FUNNY for t in tokens) or "haha" in joined
    hype = any(t in HYPE for t in tokens) or "clipit" in joined or "noway" in joined
    weight = 1.0
    if "clip" in tokens or "clipit" in joined:
        weight += 1.5
    if funny or hype:
        weight += 0.5
    if text.isupper() and len(text) > 3:
        weight += 0.3
    return weight, ("funny" if funny else "hype" if hype else None)


def _via_cli(video_id: str, work: Path) -> list[tuple[float, str]] | None:
    exe = shutil.which("TwitchDownloaderCLI")
    if not exe:
        return None
    out = work / f"chat_{video_id}.json"
    proc = subprocess.run([exe, "chatdownload", "--id", video_id, "-o", str(out)], capture_output=True, text=True)
    if proc.returncode != 0 or not out.exists():
        logger.warning("TwitchDownloaderCLI chat failed: %s", proc.stderr[-300:])
        return None
    data = json.loads(out.read_text(encoding="utf-8"))
    return [(float(c["content_offset_seconds"]), c.get("message", {}).get("body", "")) for c in data.get("comments", [])]


def _via_gql(video_id: str, budget_seconds: float) -> list[tuple[float, str]]:
    messages: list[tuple[float, str]] = []
    cursor = None
    deadline = time.time() + budget_seconds
    while time.time() < deadline:
        variables = {"videoID": video_id}
        if cursor:
            variables["cursor"] = cursor
        else:
            variables["contentOffsetSeconds"] = 0
        payload = [{
            "operationName": "VideoCommentsByOffsetOrCursor",
            "variables": variables,
            "extensions": {"persistedQuery": {"version": 1, "sha256Hash": COMMENTS_HASH}},
        }]
        r = session().post(GQL, json=payload, headers={"Client-Id": WEB_CLIENT_ID}, timeout=20)
        r.raise_for_status()
        body = r.json()
        body = body[0] if isinstance(body, list) else body
        if body.get("errors"):
            raise RuntimeError(body["errors"][0].get("message", "gql error"))
        comments = (((body.get("data") or {}).get("video") or {}).get("comments") or {})
        edges = comments.get("edges") or []
        for edge in edges:
            node = edge.get("node") or {}
            frags = (node.get("message") or {}).get("fragments") or []
            messages.append((float(node.get("contentOffsetSeconds", 0)), "".join(f.get("text", "") for f in frags)))
        if not edges or not (comments.get("pageInfo") or {}).get("hasNextPage"):
            break
        cursor = edges[-1].get("cursor")
        if not cursor:
            break
    return messages


def fetch_messages(video_id: str, work: Path, mode: str = "auto", budget_seconds: float = 180) -> list[tuple[float, str]]:
    if mode == "off":
        return []
    try:
        msgs = _via_cli(video_id, work) if mode == "auto" else None
        if msgs is None:
            msgs = _via_gql(video_id, budget_seconds)
        logger.info("chat replay: %d messages", len(msgs))
        return msgs
    except Exception as e:
        logger.warning("chat replay unavailable (%s) — continuing without chat signal", e)
        return []


def timeline(messages: list[tuple[float, str]], duration: int) -> np.ndarray:
    x = np.zeros(duration)
    for t, text in messages:
        i = int(t)
        if 0 <= i < duration:
            x[i] += classify(text)[0]
    return x


def reaction_summary(messages: list[tuple[float, str]], start: float, end: float, top: int = 8) -> dict:
    """What chat was saying during a window — great context for the judge."""
    window = [m for t, m in messages if start <= t <= end]
    kinds = Counter(k for k in (classify(m)[1] for m in window) if k)
    common = Counter(m.strip()[:40] for m in window if m.strip()).most_common(top)
    return {"messages": len(window), "kinds": dict(kinds), "top": [m for m, _ in common]}
