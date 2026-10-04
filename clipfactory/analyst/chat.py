"""Twitch chat replay → reaction timeline.

Uses TwitchDownloaderCLI when it's installed (full chat), otherwise samples Twitch's public
GraphQL comments endpoint across the whole VOD. Either way, failure just means no chat signal.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
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


@dataclass
class ChatData:
    messages: list[tuple[float, str]]
    rate: np.ndarray       # weighted chat activity per second
    coverage: np.ndarray   # seconds where we actually have chat data


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


def _gql_page(video_id: str, offset: int) -> list[tuple[float, str]]:
    payload = [{
        "operationName": "VideoCommentsByOffsetOrCursor",
        "variables": {"videoID": video_id, "contentOffsetSeconds": offset},
        "extensions": {"persistedQuery": {"version": 1, "sha256Hash": COMMENTS_HASH}},
    }]
    r = session().post(GQL, json=payload, headers={"Client-Id": WEB_CLIENT_ID}, timeout=20)
    r.raise_for_status()
    body = r.json()
    body = body[0] if isinstance(body, list) else body
    if body.get("errors"):
        raise RuntimeError(body["errors"][0].get("message", "gql error"))
    comments = (((body.get("data") or {}).get("video") or {}).get("comments") or {})
    out = []
    for edge in comments.get("edges") or []:
        node = edge.get("node") or {}
        frags = (node.get("message") or {}).get("fragments") or []
        out.append((float(node.get("contentOffsetSeconds", 0)), "".join(f.get("text", "") for f in frags)))
    return out


def _via_gql_sampled(video_id: str, duration: int, step: int = 20, workers: int = 8,
                     budget_seconds: float = 240, page=_gql_page) -> ChatData:
    """Sample one comments page every `step` seconds across the whole VOD (offset paging, like
    TwitchDownloader). Each page gives a local chat rate, so even a 10-hour stream is covered evenly."""
    rate = np.zeros(duration)
    coverage = np.zeros(duration, dtype=bool)
    messages: list[tuple[float, str]] = []
    offsets = list(range(0, duration, step))
    deadline = time.time() + budget_seconds
    errors = 0

    def work(t: int):
        if time.time() > deadline:
            return t, None
        try:
            return t, page(video_id, t)
        except Exception as e:  # noqa: BLE001
            return t, e

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for t, result in pool.map(work, offsets):
            if result is None:
                continue
            if isinstance(result, Exception):
                errors += 1
                if errors > 10 and errors > len(offsets) // 4:
                    raise result
                continue
            end = min(duration, t + step)
            coverage[t:end] = True
            inside = [(o, m) for o, m in result if o >= t]
            if not inside:
                continue
            span = max(1.0, inside[-1][0] - t + 1)
            rate[t:end] = sum(classify(m)[0] for _, m in inside) / span
            messages.extend(x for x in inside if x[0] < end)
    return ChatData(messages, rate, coverage)


def timeline(messages: list[tuple[float, str]], duration: int) -> np.ndarray:
    x = np.zeros(duration)
    for t, text in messages:
        i = int(t)
        if 0 <= i < duration:
            x[i] += classify(text)[0]
    return x


def fetch(video_id: str, duration: int, work: Path, mode: str = "auto") -> ChatData | None:
    """Full chat via TwitchDownloaderCLI when available, otherwise a sampled GQL scan. None = no chat signal."""
    if mode == "off":
        return None
    try:
        msgs = _via_cli(video_id, work) if mode == "auto" else None
        if msgs is not None:
            data = ChatData(msgs, timeline(msgs, duration), np.ones(duration, dtype=bool))
        else:
            data = _via_gql_sampled(video_id, duration)
        logger.info("chat replay: %d messages, %.0f%% of the VOD covered", len(data.messages), 100 * data.coverage.mean())
        return data if data.coverage.any() else None
    except Exception as e:
        logger.warning("chat replay unavailable (%s) — continuing without chat signal", e)
        return None


def reaction_summary(messages: list[tuple[float, str]], start: float, end: float, top: int = 8) -> dict:
    """What chat was saying during a window — great context for the judge."""
    window = [m for t, m in messages if start <= t <= end]
    kinds = Counter(k for k in (classify(m)[1] for m in window) if k)
    common = Counter(m.strip()[:40] for m in window if m.strip()).most_common(top)
    return {"messages": len(window), "kinds": dict(kinds), "top": [m for m, _ in common]}
