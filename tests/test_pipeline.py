"""End-to-end: source → analyze → judge → render → copy → QC → post (local), with Claude
and the network replaced by fakes and a synthetic video."""

import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from clipfactory import fetch
from clipfactory.analyst import transcript
from clipfactory.db import iso, loads
from clipfactory.producer import Producer
from tests.conftest import fake_words, needs_ffmpeg

SPEECH = "yo chat watch this no way he just took the cop car and drove it straight off the pier I am crying bro"


class FakeClaude:
    def __init__(self):
        self.calls = []

    def json(self, system, content, schema, effort="medium", max_tokens=16000):
        props = schema["properties"]
        self.calls.append(next(iter(props)))
        if "moments" in props:
            text = " ".join(b.get("text", "") for b in content if b.get("type") == "text")
            ids = [int(x) for x in re.findall(r"=== CANDIDATE (\d+)", text)]
            return {"moments": [
                {"candidate_id": cid, "is_gta_gameplay": True, "keep": i == 0, "virality_score": 88 if i == 0 else 40,
                 "category": "chaos", "start": 1.0, "end": 12.0, "hook_text": "HE DROVE THE COP CAR OFF THE PIER",
                 "title": "Cop car off the pier", "what_happens": "He steals a cop car and drives it off the pier.",
                 "reason": "clear payoff", "content_flags": ["none"]}
                for i, cid in enumerate(ids)
            ]}
        if "has_facecam" in props:
            return {"has_facecam": False, "x": 0, "y": 0, "w": 0, "h": 0, "confidence": "high"}
        if "youtube_title" in props:
            return {"youtube_title": "He drove the cop car off the pier", "caption": "Bro did NOT think this through 😭",
                    "engagement_question": "Would you have jumped?", "hashtags": ["#xqc", "#policechase", "#gta6"]}
        if "approve" in props:
            return {"approve": True, "issues": []}
        raise AssertionError(f"unexpected schema {props}")


def _cut(src: Path, start: float, end: float, out: Path) -> Path:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(start), "-to", str(end), "-i", str(src),
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(out)], check=True)
    return out


def _tune(cfg):
    cfg.raw["discovery"]["min_source_minutes"] = 0
    a = cfg.raw["analysis"]
    a.update(window_seconds=20, min_clip_seconds=5, max_clip_seconds=15, target_clip_seconds=10,
             skip_stream_start_seconds=0, candidates_per_source=2, judge_frames=1)


@needs_ffmpeg
def test_full_pipeline(cfg, db, sample_video, monkeypatch):
    _tune(cfg)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    heat = [{"start_time": i, "end_time": i + 1, "value": 1.0 if 20 <= i < 24 else 0.1} for i in range(40)]
    monkeypatch.setattr(fetch, "probe", lambda url, opts=None, flat=False: {
        "duration": 40, "title": "GTA 6 chaos stream", "heatmap": heat, "live_status": "was_live"})
    monkeypatch.setattr(fetch, "download_audio", lambda url, out_base, opts=None: _cut(sample_video, 0, 40, out_base.with_suffix(".mp4")))
    monkeypatch.setattr(fetch, "download_section",
                        lambda url, s, e, out_base, opts=None, max_height=1080: _cut(sample_video, s, e, out_base.with_suffix(".mp4")))
    monkeypatch.setattr(transcript, "transcribe", lambda path, size="small", language="en": fake_words(SPEECH, 0.5, 0.45))

    claude = FakeClaude()
    p = Producer(cfg, db, claude)
    sid = db.add_source("youtube", "vid1", "xQc", "GTA 6 chaos stream", "https://youtube.com/watch?v=vid1",
                        iso(), 40, "video", {})
    assert sid

    assert p.produce(1) == 1
    assert claude.calls[:1] == ["moments"] and "youtube_title" in claude.calls and "approve" in claude.calls

    clip = db.clips("approved")[0]
    assert fetch.video_size(clip["path"]) == (1080, 1920)
    assert 5 <= clip["duration"] <= 16
    copy = loads(clip["copy"])
    assert "🎥 @xqc on YouTube" in copy["instagram_caption"]
    assert copy["youtube_title"].endswith("#shorts")
    assert db.one("SELECT status FROM sources WHERE id = ?", [sid])["status"] == "analyzed"
    rejected = db.all("SELECT * FROM moments WHERE status = 'rejected'")
    assert all(not Path(m["local_path"]).exists() for m in rejected)  # cleaned up

    # post into a slot via the local publisher
    posted = p.post(clip, "2026-11-19T12:00")
    assert posted == clip["id"]
    assert db.slot_filled("2026-11-19T12:00")
    ready = list((cfg.home / "out" / "ready").iterdir())
    assert len(ready) == 1 and (ready[0] / "video.mp4").exists()
    assert "@xqc" in (ready[0] / "caption_tiktok.txt").read_text()
    assert db.clip(clip["id"])["status"] == "published"


def test_publish_due_picks_best_and_expires_old(cfg, db, monkeypatch):
    p = Producer(cfg, db, FakeClaude())
    now = datetime(2026, 11, 19, 17, 5, tzinfo=timezone.utc)  # 12:05 in New York
    fresh = db.add_source("twitch", "1", "xQc", "t", "u1", iso(now - timedelta(hours=5)), 9000, "vod", {})
    stale = db.add_source("twitch", "2", "summit1g", "t", "u2", iso(now - timedelta(hours=200)), 9000, "vod", {})
    clips = {}
    for name, sid, score in (("fresh", fresh, 80), ("stale", stale, 95)):
        mid = db.add_moment(sid, 0, 60, {})
        cid = db.add_clip(mid, "x", "/nonexistent.mp4", None, 30, score)
        db.update("clips", cid, status="approved", copy=json.dumps({"instagram_caption": "c"}))
        clips[name] = cid
    p.expire(now)
    assert db.clip(clips["stale"])["status"] == "expired"
    monkeypatch.setattr(p, "post", lambda clip, slot: (clip["id"], slot))
    assert p.publish_due(now) == (clips["fresh"], "2026-11-19T12:00")


def test_produce_skips_without_claude_credentials(cfg, db, monkeypatch, tmp_path):
    from clipfactory import producer as producer_mod

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(producer_mod.Path, "home", lambda: tmp_path)
    db.add_source("youtube", "v", "xQc", "GTA 6", "u", iso(), 600, "video", {})
    called = []
    monkeypatch.setattr(producer_mod.analyst, "analyze_source", lambda *a: called.append(1) or [])
    assert Producer(cfg, db, FakeClaude()).produce(3) == 0
    assert not called


def test_cleanup_removes_old_finished_media(cfg, db):
    old = datetime.now(timezone.utc) + timedelta(days=30)
    clip_file = cfg.path("out") / "clips" / "old.mp4"
    clip_file.parent.mkdir(parents=True, exist_ok=True)
    clip_file.write_bytes(b"x")
    sid = db.add_source("twitch", "9", "xQc", "t", "u", iso(), 9000, "vod", {})
    cid = db.add_clip(db.add_moment(sid, 0, 60, {}), "xQc", str(clip_file), None, 30, 80)
    db.update("clips", cid, status="published")
    Producer(cfg, db, FakeClaude()).cleanup(old)
    assert not clip_file.exists()
