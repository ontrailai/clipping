"""🎬 Producer — runs the crew. One `tick()` does whatever is due:

  1. Scout for new uploads/VODs (every discovery.interval_hours)
  2. Keep the queue stocked: analyze → edit → write copy → QC until there are enough
     approved clips for the rest of today plus a buffer
  3. Post the next clip if a slot is due

Run it from cron/Task Scheduler/GitHub Actions, or `clipfactory daemon` to loop forever.
"""

from __future__ import annotations

import os
import shutil
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import analyst, copywriter, editor, log, publisher, qc, scheduler, scout
from .analyst import transcript
from .config import Config
from .db import DB, iso, loads, parse_iso
from .llm import Claude, LLMError, LLMRefusal
from .publisher import AuthError, PublishPending

logger = log.get("producer")


class Producer:
    def __init__(self, cfg: Config, db: DB | None = None, claude: Claude | None = None):
        self.cfg = cfg
        self.db = db or DB(cfg.path("data") / "state.db")
        self.claude = claude or Claude(cfg["llm"]["model"])

    # ------------------------------------------------------------------ tick
    def tick(self, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        summary = {"discovered": 0, "produced": 0, "posted": None}
        try:
            with tick_lock(self.cfg.path("data") / "tick.lock"):
                if self.discovery_due(now):
                    summary["discovered"] = scout.discover(self.cfg, self.db)
                self.expire(now)
                self.follow_up_posts(now)
                summary["posted"] = self.publish_due(now)  # post first: production can take an hour
                want = self.needed(now)
                if want > 0:
                    logger.info("queue needs %d more clip(s)", want)
                    summary["produced"] = self.produce(want)
                if summary["posted"] is None:
                    summary["posted"] = self.publish_due(datetime.now(timezone.utc))
                self.cleanup(now)
        except TickBusy:
            logger.info("another tick is still running — skipping")
            summary["skipped"] = True
        return summary

    def discovery_due(self, now: datetime) -> bool:
        last = parse_iso(self.db.kv_get("last_discovery_at"))
        return last is None or now - last >= timedelta(hours=self.cfg["discovery"]["interval_hours"])

    # ------------------------------------------------------------------ inventory
    def ready_clips(self) -> list[dict]:
        return self.db.clips("approved")

    def needed(self, now: datetime) -> int:
        target = scheduler.remaining_today(self.cfg, self.db, now) + self.cfg["buffer_clips"]
        have = len(self.ready_clips()) + len(self.db.clips("pending_review"))
        return max(0, target - have)

    def expire(self, now: datetime) -> None:
        max_age = timedelta(hours=self.cfg["max_clip_age_hours"])
        for clip in self.db.clips("approved") + self.db.clips("pending_review") + self.db.clips("rendered"):
            src = self._source_for_clip(clip)
            published = parse_iso(src.get("published_at")) if src else None
            if published and now - published > max_age:
                self.db.update("clips", clip["id"], status="expired")
                logger.info("clip #%s expired (moment is %dh old)", clip["id"], (now - published).total_seconds() // 3600)
        stale = now - timedelta(hours=self.cfg["discovery"]["lookback_hours"] * 1.5)
        for s in self.db.pending_sources():
            p = parse_iso(s.get("published_at"))
            if p and p < stale:
                self.db.update("sources", s["id"], status="skipped", error="stale")

    def cleanup(self, now: datetime) -> None:
        """Delete media we no longer need: finished clips, ready-folders and work dirs past keep_files_days."""
        cutoff = now - timedelta(days=self.cfg["keep_files_days"])
        for clip in self.db.all("SELECT * FROM clips WHERE status IN ('published','expired','rejected','failed')"):
            created = parse_iso(clip["created_at"])
            if created and created < cutoff:
                for f in (clip["path"], clip["cover_path"], str(Path(clip["path"]).with_suffix(".ass"))):
                    if f:
                        Path(f).unlink(missing_ok=True)
        roots = [self.cfg.path("work")]
        ready = Path(self.cfg["platforms"]["local"].get("dir", "out/ready"))
        roots.append(ready if ready.is_absolute() else self.cfg.home / ready)
        busy = {f"src_{r['source_id']}" for r in self.db.all("SELECT DISTINCT source_id FROM moments WHERE status = 'selected'")}
        for root in roots:
            if not root.exists():
                continue
            for d in root.iterdir():
                if d.is_dir() and d.name not in busy and datetime.fromtimestamp(d.stat().st_mtime, timezone.utc) < cutoff:
                    shutil.rmtree(d, ignore_errors=True)

    def _source_for_clip(self, clip: dict) -> dict | None:
        return self.db.one(
            "SELECT s.* FROM sources s JOIN moments m ON m.source_id = s.id WHERE m.id = ?", [clip["moment_id"]]
        )

    # ------------------------------------------------------------------ production
    def produce(self, want: int) -> int:
        made = 0
        if not _claude_credentials():
            logger.error("no Claude credentials (ANTHROPIC_API_KEY) — the Analyst can't judge clips; skipping production")
            return 0
        for clip in self.db.clips("rendered"):  # rendered earlier but QC couldn't run
            if made >= want:
                return made
            made += self._safe_finish(clip["id"])
        for m in self.db.all("SELECT * FROM moments WHERE status = 'selected' ORDER BY score DESC"):
            if made >= want:
                return made
            made += self._render_and_finish(m)

        def priority(s: dict) -> tuple:
            c = self.cfg.creator(s["creator"])
            return (c.priority if c else 9, -(parse_iso(s.get("published_at")) or datetime.min.replace(tzinfo=timezone.utc)).timestamp())

        for source in sorted(self.db.pending_sources(), key=priority):
            if made >= want:
                break
            try:
                selected = analyst.analyze_source(self.cfg, self.db, self.claude, source)
            except Exception as e:
                logger.exception("analysis crashed on source %s", source["id"])
                self.db.update("sources", source["id"], status="failed", error=str(e)[:500])
                continue
            for mid in selected:
                if made >= want:
                    break  # leftovers stay 'selected' for the next tick
                m = self.db.one("SELECT * FROM moments WHERE id = ?", [mid])
                made += self._render_and_finish(m)
        if made < want:
            logger.info("only %d/%d clips made — waiting for new uploads", made, want)
        return made

    def _render_and_finish(self, moment: dict) -> int:
        """Never lets one bad moment crash the tick (and block posting)."""
        try:
            clip_id = editor.render_moment(self.cfg, self.db, self.claude, moment)
        except Exception as e:
            logger.exception("render crashed on moment %s", moment["id"])
            self.db.update("moments", moment["id"], status="failed", error=str(e)[:500])
            return 0
        return self._safe_finish(clip_id) if clip_id else 0

    def _safe_finish(self, clip_id: int) -> int:
        try:
            return 1 if self.finish(clip_id) in ("approved", "pending_review") else 0
        except Exception as e:
            logger.exception("copy/QC crashed on clip %s", clip_id)
            self.db.update("clips", clip_id, status="failed", qc={"passed": False, "issues": [str(e)[:300]]})
            return 0

    def finish(self, clip_id: int) -> str:
        """Copywriter + QC for a rendered clip; returns its new status."""
        clip = self.db.clip(clip_id)
        moment = self.db.one("SELECT * FROM moments WHERE id = ?", [clip["moment_id"]])
        source = self.db.one("SELECT * FROM sources WHERE id = ?", [moment["source_id"]])
        words = transcript.window(loads(moment["words"], []), moment["clip_start"], moment["clip_end"])
        said = " ".join(w["w"] for w in words)
        creator = self.cfg.creator(source["creator"])
        credit = creator.credit_handle if creator else source["creator"]

        try:
            copy = copywriter.write_copy(self.claude, self.cfg, moment, source, credit, said)
        except LLMError as e:
            logger.warning("copywriter fell back to template: %s", e)
            copy = copywriter.assemble({"caption": moment.get("hook") or moment.get("title")}, credit=credit,
                                       platform=source["platform"], brand=self.cfg["brand"],
                                       title_fallback=moment.get("title") or "")
        try:
            report = qc.run(self.claude, self.cfg, clip, moment, copy, words, said)
        except LLMRefusal as e:
            report = {"passed": False, "issues": [f"QC reviewer declined: {e}"]}
        except LLMError as e:
            attempts = int(self.db.kv_get(f"qc_attempts:{clip_id}", "0")) + 1
            self.db.kv_set(f"qc_attempts:{clip_id}", str(attempts))
            report = {"passed": False, "issues": [f"QC reviewer unavailable ({attempts}/3): {e}"], "retry": attempts < 3}

        if report["passed"]:
            status = "approved" if self.cfg["qc"]["approval"] == "auto" else "pending_review"
        else:
            status = "rendered" if report.get("retry") else "rejected"
        self.db.update("clips", clip_id, copy=copy, qc=report, status=status)
        lg = logger.info if report["passed"] else logger.warning
        lg("clip #%s %s — %s", clip_id, status.upper(), "; ".join(report["issues"]) or copy["youtube_title"])
        return status

    # ------------------------------------------------------------------ publishing
    def pick_next(self) -> dict | None:
        clips = self.ready_clips()
        if not clips:
            return None
        today = datetime.now(self.cfg.tz).strftime("%Y-%m-%d")
        rows = self.db.all(
            "SELECT c.creator, COUNT(*) AS n FROM slots s JOIN clips c ON c.id = s.clip_id WHERE s.slot_key LIKE ? GROUP BY c.creator",
            [f"{today}%"],
        )
        posted_today = {r["creator"]: r["n"] for r in rows}
        cap = self.cfg["analysis"]["max_clips_per_creator_per_day"]
        fresh = [c for c in clips if posted_today.get(c["creator"], 0) < cap]
        return (fresh or clips)[0]  # already sorted by score, then recency

    def publish_due(self, now: datetime | None = None, force: bool = False) -> int | None:
        slot = scheduler.due_slot(self.cfg, self.db, now) if not force else f"manual-{iso()}"
        if not slot:
            return None
        clip = self.pick_next()
        if not clip:
            logger.warning("slot %s is due but nothing is approved yet", slot)
            return None
        return self.post(clip, slot)

    def post(self, clip: dict, slot: str) -> int | None:
        logger.info("posting clip #%s for slot %s", clip["id"], slot)
        copy = loads(clip["copy"], {})
        pubs = publisher.enabled(self.cfg)
        done = {p["platform"] for p in self.db.posts_for(clip["id"]) if p["status"] in ("published", "pending")}
        social_ok, real_failures = False, 0
        for pub in pubs:
            if pub.name in done:
                social_ok |= pub.name != "local"
                continue
            ok, why = pub.ready()
            if not ok:
                logger.error("%s not connected: %s", pub.name, why)
                self.db.record_post(clip["id"], pub.name, "failed", error=why)
                continue
            try:
                res = pub.publish(clip, copy, slot)
                self.db.record_post(clip["id"], pub.name, "published", res.remote_id, res.url)
                logger.info("%s ✓ %s", pub.name, res.url or res.remote_id or "")
                social_ok |= pub.name != "local"
            except PublishPending as e:  # uploaded; the platform is still processing — check later, never re-upload
                self.db.record_post(clip["id"], pub.name, "pending", remote_id=e.remote_id, error=str(e))
                logger.info("%s … still processing (%s)", pub.name, e.remote_id)
                social_ok |= pub.name != "local"
            except AuthError as e:  # a login problem isn't the clip's fault: don't burn the clip
                self.db.record_post(clip["id"], pub.name, "failed", error=str(e)[:500])
                logger.error("%s login problem: %s", pub.name, e)
            except Exception as e:
                self.db.record_post(clip["id"], pub.name, "failed", error=str(e)[:500])
                logger.warning("%s ✗ %s", pub.name, e)
                real_failures += 1

        social = [p for p in pubs if p.name != "local"]
        if social_ok or not social:
            self.db.update("clips", clip["id"], status="published", published_at=iso())
            self.db.fill_slot(slot, clip["id"])
            return clip["id"]
        if real_failures:
            attempts = int(self.db.kv_get(f"attempts:{clip['id']}", "0")) + 1
            self.db.kv_set(f"attempts:{clip['id']}", str(attempts))
            if attempts >= 3:
                self.db.update("clips", clip["id"], status="failed")
        return None

    def follow_up_posts(self, now: datetime) -> None:
        """Finish uploads that were still processing, and give platforms that failed two more tries."""
        since = iso(now - timedelta(hours=24))
        rows = self.db.all(
            "SELECT p.* FROM posts p JOIN clips c ON c.id = p.clip_id"
            " WHERE p.status IN ('failed', 'pending') AND c.status = 'published' AND c.published_at >= ?", [since],
        )
        pubs = {p.name: p for p in publisher.enabled(self.cfg)}
        for row in rows:
            pub = pubs.get(row["platform"])
            if not pub or not pub.ready()[0]:
                continue
            clip = self.db.clip(row["clip_id"])
            try:
                if row["status"] == "pending":
                    res = pub.check(row["remote_id"])
                else:
                    key = f"retry:{row['clip_id']}:{row['platform']}"
                    tries = int(self.db.kv_get(key, "0"))
                    if tries >= 2:
                        continue
                    self.db.kv_set(key, str(tries + 1))
                    res = pub.publish(clip, loads(clip["copy"], {}), "retry")
                self.db.record_post(clip["id"], pub.name, "published", res.remote_id, res.url)
                logger.info("%s ✓ clip #%s (follow-up)", pub.name, clip["id"])
            except PublishPending as e:
                self.db.record_post(clip["id"], pub.name, "pending", remote_id=e.remote_id, error=str(e))
            except Exception as e:
                self.db.record_post(clip["id"], pub.name, "failed", remote_id=row["remote_id"], error=str(e)[:500])

    # ------------------------------------------------------------------ one-offs
    def add_url(self, url: str, creator: str | None = None) -> int | None:
        """Queue any YouTube/Twitch/Kick URL by hand (bypasses discovery filters)."""
        platform, ext, canonical, slug = parse_video_url(url)
        meta: dict = {}
        if platform == "twitch":
            try:
                meta = _twitch_meta(ext)
            except Exception as e:
                logger.info("no Twitch community clips for manual URL (%s)", e)
        if not creator:
            known = self.cfg.creator_for(platform, slug or meta.get("user_login", "")) if (slug or meta.get("user_login")) else None
            creator = known.name if known else (meta.get("user_name") or slug or "unknown")
        sid = self.db.add_source(platform, ext, creator, "", canonical, iso(), None,
                                 "vod" if platform in ("twitch", "kick") else "video", meta)
        if sid is None:
            row = self.db.one("SELECT id FROM sources WHERE platform = ? AND external_id = ?", [platform, ext])
            self.db.update("sources", row["id"], status="new")
            sid = row["id"]
        return sid


def parse_video_url(url: str) -> tuple[str, str, str, str | None]:
    """URL -> (platform, external id, canonical url, channel slug if known). Query strings are dropped."""
    u = urlparse(url.strip())
    host = u.netloc.lower()
    parts = [p for p in u.path.split("/") if p]
    if "youtu.be" in host and parts:
        vid = parts[0]
        return "youtube", vid, f"https://www.youtube.com/watch?v={vid}", None
    if "youtube.com" in host:
        vid = parse_qs(u.query).get("v", [None])[0]
        if not vid and len(parts) >= 2 and parts[0] in ("shorts", "live", "embed"):
            vid = parts[1]
        if vid:
            return "youtube", vid, f"https://www.youtube.com/watch?v={vid}", None
    if "twitch.tv" in host and "videos" in parts and parts.index("videos") + 1 < len(parts):
        vid = parts[parts.index("videos") + 1]
        return "twitch", vid, f"https://www.twitch.tv/videos/{vid}", None
    if "kick.com" in host and len(parts) >= 3 and parts[1] == "videos":
        return "kick", parts[2], f"https://kick.com/{parts[0]}/videos/{parts[2]}", parts[0]
    return "other", (host + u.path).strip("/"), url, None


class TickBusy(RuntimeError):
    pass


@contextmanager
def tick_lock(path: Path, stale_after: float = 8 * 3600):
    """Stops overlapping ticks (cron + a long production run) from double-posting a slot."""
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            age = time.time() - path.stat().st_mtime
        except FileNotFoundError:
            age = stale_after
        if age < stale_after:
            raise TickBusy() from None
        path.unlink(missing_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def _claude_credentials() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                or (Path.home() / ".config" / "anthropic").exists())


def _twitch_meta(video_id: str) -> dict:
    from .scout.twitch import Twitch, clip_summary, parse_duration

    tw = Twitch()
    if not tw.configured:
        return {}
    vod = tw.videos_by_id([video_id])[0]
    created = parse_iso(vod["created_at"])
    dur = parse_duration(vod["duration"])
    clips = [clip_summary(c) for c in tw.clips(broadcaster_id=vod["user_id"], started_at=created,
                                                 ended_at=created + timedelta(seconds=dur + 600))
             if c.get("video_id") == video_id and c.get("vod_offset") is not None]
    return {"community_clips": clips, "user_login": vod.get("user_login"), "user_name": vod.get("user_name")}
