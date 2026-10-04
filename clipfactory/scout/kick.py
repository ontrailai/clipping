"""Kick discovery via the public JSON endpoints kick.com itself uses.

Kick sits behind Cloudflare; curl_cffi (installed via yt-dlp's curl-cffi extra) lets us
present a real browser TLS fingerprint, which helps a lot.
"""

from __future__ import annotations

from ..db import parse_iso
from ..http import session

BASE = "https://kick.com/api/v2/channels"


def _get_json(url: str, params: dict | None = None) -> dict | list:
    try:
        from curl_cffi import requests as cffi  # type: ignore

        r = cffi.get(url, params=params, impersonate="chrome", timeout=25)
    except ImportError:
        r = session().get(url, params=params, timeout=25, headers={"Accept": "application/json"})
    if r.status_code != 200:
        raise RuntimeError(f"kick {url} -> HTTP {r.status_code}")
    return r.json()


def videos(slug: str) -> list[dict]:
    data = _get_json(f"{BASE}/{slug}/videos")
    return data if isinstance(data, list) else data.get("data", [])


def clips(slug: str, time: str = "week") -> list[dict]:
    data = _get_json(f"{BASE}/{slug}/clips", {"sort": "view", "time": time})
    if isinstance(data, dict):
        return data.get("clips") or data.get("data") or []
    return data


def vod_url(slug: str, item: dict) -> str | None:
    uuid = (item.get("video") or {}).get("uuid")
    return f"https://kick.com/{slug}/videos/{uuid}" if uuid else None


def vod_categories(item: dict) -> list[str]:
    return [c.get("name", "") for c in item.get("categories") or [] if c]


def clip_offset(clip: dict, vod: dict) -> float | None:
    """Kick clips carry wall-clock timestamps; turn them into a VOD offset."""
    vod_start = parse_iso((vod.get("start_time") or vod.get("created_at") or "").replace(" ", "T"))
    if not vod_start:
        return None
    started = parse_iso((clip.get("started_at") or "").replace(" ", "T"))
    if started:
        return max(0.0, (started - vod_start).total_seconds())
    created = parse_iso((clip.get("created_at") or "").replace(" ", "T"))
    if created:
        return max(0.0, (created - vod_start).total_seconds() - float(clip.get("duration") or 30))
    return None


def clip_summary(clip: dict, vod: dict) -> dict:
    return {
        "offset": clip_offset(clip, vod),
        "duration": float(clip.get("duration") or 30),
        "views": int(clip.get("views") or clip.get("view_count") or 0),
        "title": clip.get("title", ""),
        "game": (clip.get("category") or {}).get("name"),
        "url": clip.get("clip_url") or clip.get("video_url"),
    }
