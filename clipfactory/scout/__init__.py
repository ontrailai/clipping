"""🔭 Scout — finds fresh GTA uploads, VODs and the crowd's favourite moments."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from .. import fetch, log
from ..config import Config
from ..db import DB, iso, parse_iso
from . import kick, twitch, youtube

logger = log.get("scout")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def is_gta_category(name: str | None, games: list[str]) -> bool:
    n = _norm(name or "")
    if not n:
        return False
    return n in {_norm(g) for g in games} or "grandtheftauto" in n or n.startswith("gta")


def title_matches(title: str, keywords: list[str]) -> bool:
    t = f" {(title or '').lower()} "
    return any(re.search(rf"(?<![a-z0-9]){re.escape(k.lower())}(?![a-z0-9])", t) for k in keywords)


def discover(cfg: Config, db: DB) -> int:
    """Run every source; returns the number of new sources queued for analysis."""
    found = 0
    for name, fn in (("youtube", _youtube), ("twitch", _twitch), ("kick", _kick)):
        try:
            n = fn(cfg, db)
            found += n
        except Exception as e:  # one platform being down shouldn't stop the others
            logger.warning("%s discovery failed: %s", name, e)
    db.kv_set("last_discovery_at", iso())
    logger.info("discovery done — %d new videos/VODs queued", found)
    return found


def _cutoff(cfg: Config) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=cfg["discovery"]["lookback_hours"])


def _duration_ok(cfg: Config, seconds: float | None) -> bool:
    if seconds is None:
        return True  # unknown yet; Analyst re-checks after probing
    d = cfg["discovery"]
    return d["min_source_minutes"] * 60 <= seconds <= d["max_source_hours"] * 3600


# ---------------------------------------------------------------------------- YouTube
def _youtube(cfg: Config, db: DB) -> int:
    opts = fetch.ytdlp_opts(cfg)
    cutoff, found = _cutoff(cfg), 0
    for creator in cfg.enabled_creators:
        for handle in creator.source_ids("youtube"):
            try:
                channel_id = youtube.resolve_channel_id(db, handle, opts)
                entries = youtube.fetch_feed(channel_id)
            except Exception as e:
                logger.warning("youtube %s (%s): %s", creator.name, handle, e)
                continue
            for e in entries:
                if e.published and e.published < cutoff:
                    continue
                if not title_matches(e.title, cfg["keywords"]):
                    continue
                sid = db.add_source("youtube", e.video_id, creator.name, e.title, e.url,
                                    iso(e.published) if e.published else None, None, "video",
                                    {"views": e.views})
                if sid:
                    found += 1
                    logger.info("new YouTube upload: %s — %s", creator.name, e.title)
    return found


# ---------------------------------------------------------------------------- Twitch
def _twitch(cfg: Config, db: DB) -> int:
    tw = twitch.Twitch()
    if not tw.configured:
        logger.info("Twitch keys not set — skipping Twitch")
        return 0
    games = cfg["games"]
    game_ids = tw.game_ids(games)
    id_to_game = {v: k for k, v in game_ids.items()}
    cutoff, found = _cutoff(cfg), 0

    logins = {login.lower(): c for c in cfg.enabled_creators for login in c.source_ids("twitch")}
    users = tw.users(list(logins)) if logins else {}
    live = tw.live_streams([u["id"] for u in users.values()])

    def consider(vod: dict, creator_name: str) -> bool:
        created = parse_iso(vod["created_at"])
        duration = twitch.parse_duration(vod.get("duration", ""))
        if created is None or created < cutoff or not _duration_ok(cfg, duration):
            return False
        stream = live.get(vod["user_id"])
        if stream and abs((parse_iso(stream["started_at"]) - created).total_seconds()) < 900:
            return False  # still live — wait for the full VOD
        clips = [c for c in tw.clips(broadcaster_id=vod["user_id"], started_at=created,
                                     ended_at=created + timedelta(seconds=duration + 600))
                 if c.get("video_id") == vod["id"] and c.get("vod_offset") is not None]
        summaries = []
        for c in clips:
            s = twitch.clip_summary(c)
            s["game"] = id_to_game.get(c.get("game_id"), c.get("game_id"))
            s["is_gta"] = c.get("game_id") in id_to_game
            summaries.append(s)
        gta_clips = sum(1 for s in summaries if s["is_gta"])
        if not gta_clips and not title_matches(vod.get("title", ""), cfg["keywords"]):
            return False
        sid = db.add_source("twitch", vod["id"], creator_name, vod.get("title", ""), vod["url"],
                            vod["created_at"], duration, "vod",
                            {"community_clips": summaries, "views": vod.get("view_count"), "user_id": vod["user_id"]})
        if sid:
            logger.info("new Twitch VOD: %s — %s (%d GTA community clips)", creator_name, vod.get("title", ""), gta_clips)
        return bool(sid)

    for login, creator in logins.items():
        user = users.get(login)
        if not user:
            logger.warning("twitch user not found: %s", login)
            continue
        try:
            vods = tw.vods(user["id"])
        except Exception as e:
            logger.warning("twitch %s: %s", login, e)
            continue
        for vod in vods:
            try:
                found += consider(vod, creator.name)
            except Exception as e:
                logger.warning("twitch vod %s: %s", vod.get("id"), e)

    if cfg["discovery"].get("twitch_category_scan") and game_ids:
        try:
            found += _twitch_category_scan(cfg, db, tw, game_ids, users, consider, cutoff)
        except Exception as e:
            logger.warning("twitch category scan failed: %s", e)
    return found


def _twitch_category_scan(cfg, db, tw, game_ids, users, consider, cutoff) -> int:
    """Top GTA clips across Twitch → their VODs (only for listed creators unless creator_policy: open)."""
    found = 0
    open_policy = cfg["discovery"].get("creator_policy") == "open"
    allowed_ids = {u["id"] for u in users.values()}
    video_ids: set[str] = set()
    for gid in game_ids.values():
        for c in tw.clips(game_id=gid, started_at=cutoff, ended_at=datetime.now(timezone.utc), max_pages=2):
            if c.get("video_id") and (open_policy or c["broadcaster_id"] in allowed_ids):
                video_ids.add(c["video_id"])
    known = {r["external_id"] for r in db.all("SELECT external_id FROM sources WHERE platform='twitch'")}
    for vod in tw.videos_by_id([v for v in video_ids if v not in known]):
        creator = cfg.creator_for("twitch", vod["user_login"])
        if creator is None and not open_policy:
            continue
        try:
            found += consider(vod, creator.name if creator else vod["user_name"])
        except Exception as e:
            logger.warning("twitch vod %s: %s", vod.get("id"), e)
    return found


# ---------------------------------------------------------------------------- Kick
def _kick(cfg: Config, db: DB) -> int:
    cutoff, found = _cutoff(cfg), 0
    for creator in cfg.enabled_creators:
        for slug in creator.source_ids("kick"):
            try:
                vods = kick.videos(slug)
            except Exception as e:
                logger.warning("kick %s: %s", slug, e)
                continue
            try:
                clips = kick.clips(slug)
            except Exception:
                clips = []
            for vod in vods:
                try:
                    found += _kick_vod(cfg, db, creator, slug, vod, clips, cutoff)
                except Exception as e:
                    logger.warning("kick vod %s: %s", vod.get("id"), e)
    return found


def _kick_vod(cfg, db, creator, slug, vod, clips, cutoff) -> int:
    if vod.get("is_live"):
        return 0
    created = parse_iso((vod.get("start_time") or vod.get("created_at") or "").replace(" ", "T"))
    duration = (vod.get("duration") or 0) / 1000 or None
    url = kick.vod_url(slug, vod)
    if not url or created is None or created < cutoff or not _duration_ok(cfg, duration):
        return 0
    cats = kick.vod_categories(vod)
    if not any(is_gta_category(c, cfg["games"]) for c in cats) and not title_matches(vod.get("session_title", ""), cfg["keywords"]):
        return 0
    mine = [c for c in clips if str(c.get("livestream_id")) == str(vod.get("id"))]
    summaries = [kick.clip_summary(c, vod) for c in mine]
    for s in summaries:
        s["is_gta"] = is_gta_category(s.get("game"), cfg["games"]) if s.get("game") else True
    summaries = [s for s in summaries if s["offset"] is not None]
    ext_id = (vod.get("video") or {}).get("uuid") or str(vod.get("id"))
    sid = db.add_source("kick", ext_id, creator.name, vod.get("session_title", ""), url,
                        iso(created), duration, "vod", {"community_clips": summaries, "categories": cats})
    if sid:
        logger.info("new Kick VOD: %s — %s", creator.name, vod.get("session_title", ""))
    return 1 if sid else 0
