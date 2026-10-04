"""YouTube discovery via the public channel RSS feed (no API key, no quota)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime

from .. import fetch
from ..db import DB, parse_iso
from ..http import session

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}


@dataclass
class FeedEntry:
    video_id: str
    title: str
    published: datetime | None
    url: str
    views: int | None


def parse_feed(xml_text: str) -> list[FeedEntry]:
    root = ET.fromstring(xml_text)
    entries = []
    for e in root.findall("atom:entry", NS):
        vid = e.findtext("yt:videoId", default="", namespaces=NS)
        title = e.findtext("atom:title", default="", namespaces=NS)
        published = parse_iso(e.findtext("atom:published", default=None, namespaces=NS))
        stats = e.find("media:group/media:community/media:statistics", NS)
        views = int(stats.get("views")) if stats is not None and stats.get("views") else None
        entries.append(FeedEntry(vid, title, published, f"https://www.youtube.com/watch?v={vid}", views))
    return entries


def resolve_channel_id(db: DB, handle_or_id: str, ytdlp_opts: dict) -> str:
    """'@Handle' or a UC... id -> channel id (cached)."""
    if handle_or_id.startswith("UC") and len(handle_or_id) == 24:
        return handle_or_id
    key = f"yt_channel:{handle_or_id.lower()}"
    cached = db.kv_get(key)
    if cached:
        return cached
    handle = handle_or_id if handle_or_id.startswith("@") else f"@{handle_or_id}"
    info = fetch.probe(f"https://www.youtube.com/{handle}/videos", ytdlp_opts, flat=True)
    channel_id = info.get("channel_id") or info.get("uploader_id") or ""
    if not channel_id.startswith("UC"):
        raise RuntimeError(f"could not resolve YouTube channel for {handle_or_id}")
    db.kv_set(key, channel_id)
    return channel_id


def fetch_feed(channel_id: str) -> list[FeedEntry]:
    r = session().get("https://www.youtube.com/feeds/videos.xml", params={"channel_id": channel_id}, timeout=20)
    r.raise_for_status()
    return parse_feed(r.text)
