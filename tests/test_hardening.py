"""Regression tests for review findings: URL parsing, tick lock, auth-aware posting,
pending uploads, chat sampling coverage, coverage-aware fusion, filter path quoting."""

import json

import numpy as np
import pytest

from clipfactory.analyst import chat, signals
from clipfactory.db import iso
from clipfactory.editor.layout import ff_escape
from clipfactory.producer import Producer, TickBusy, parse_video_url, tick_lock
from clipfactory.publisher.base import AuthError, PostResult, Publisher, PublishPending


@pytest.mark.parametrize("url,expected", [
    ("https://www.youtube.com/watch?v=abc123&t=42s", ("youtube", "abc123", "https://www.youtube.com/watch?v=abc123", None)),
    ("https://youtu.be/abc123?si=xyz", ("youtube", "abc123", "https://www.youtube.com/watch?v=abc123", None)),
    ("https://www.youtube.com/live/abc123?feature=share", ("youtube", "abc123", "https://www.youtube.com/watch?v=abc123", None)),
    ("https://www.twitch.tv/videos/2233445566?t=1h2m", ("twitch", "2233445566", "https://www.twitch.tv/videos/2233445566", None)),
    ("https://kick.com/xqc/videos/1b2c-3d4e?x=1", ("kick", "1b2c-3d4e", "https://kick.com/xqc/videos/1b2c-3d4e", "xqc")),
])
def test_parse_video_url(url, expected):
    assert parse_video_url(url) == expected


def test_tick_lock_blocks_overlap(tmp_path):
    lock = tmp_path / "tick.lock"
    with tick_lock(lock):
        with pytest.raises(TickBusy):
            with tick_lock(lock):
                pass
    assert not lock.exists()
    with tick_lock(lock):  # free again
        pass


class FakePub(Publisher):
    def __init__(self, cfg, name, behaviour):
        self.name = name
        super().__init__(cfg)
        self.behaviour = behaviour
        self.uploads = 0

    def publish(self, clip, copy, slot_key):
        self.uploads += 1
        if self.behaviour == "auth":
            raise AuthError("token expired")
        if self.behaviour == "pending":
            raise PublishPending("pub-123")
        if self.behaviour == "error":
            raise RuntimeError("500 from platform")
        return PostResult("remote-1", "https://example.com/1")

    def check(self, remote_id):
        return PostResult("final-9", None)


def _clip(db):
    sid = db.add_source("twitch", "77", "xQc", "t", "u", iso(), 9000, "vod", {})
    cid = db.add_clip(db.add_moment(sid, 0, 60, {}), "xQc", "/x.mp4", None, 30, 80)
    db.update("clips", cid, status="approved", copy=json.dumps({"instagram_caption": "c"}))
    return db.clip(cid)


def _with_pubs(monkeypatch, pubs):
    from clipfactory import producer as producer_mod
    monkeypatch.setattr(producer_mod.publisher, "enabled", lambda cfg: pubs)


def test_auth_failure_does_not_burn_the_clip(cfg, db, monkeypatch):
    pubs = [FakePub(cfg, "instagram", "auth")]
    _with_pubs(monkeypatch, pubs)
    p = Producer(cfg, db, None)
    clip = _clip(db)
    for _ in range(5):
        assert p.post(clip, "2026-11-19T12:00") is None
    assert db.clip(clip["id"])["status"] == "approved"  # still queued for when the login is fixed
    assert not db.slot_filled("2026-11-19T12:00")


def test_real_failures_retire_clip_after_three(cfg, db, monkeypatch):
    _with_pubs(monkeypatch, [FakePub(cfg, "instagram", "error")])
    p = Producer(cfg, db, None)
    clip = _clip(db)
    for _ in range(3):
        p.post(clip, "2026-11-19T12:00")
    assert db.clip(clip["id"])["status"] == "failed"


def test_pending_upload_fills_slot_then_resolves_without_reupload(cfg, db, monkeypatch):
    tiktok = FakePub(cfg, "tiktok", "pending")
    _with_pubs(monkeypatch, [tiktok])
    p = Producer(cfg, db, None)
    clip = _clip(db)
    assert p.post(clip, "2026-11-19T12:00") == clip["id"]
    assert db.posts_for(clip["id"])[0]["status"] == "pending"
    from datetime import datetime, timezone
    p.follow_up_posts(datetime.now(timezone.utc))
    post = db.posts_for(clip["id"])[0]
    assert post["status"] == "published" and post["remote_id"] == "final-9"
    assert tiktok.uploads == 1


def test_chat_sampling_covers_whole_vod():
    def page(video_id, offset):
        busy = 3000 <= offset < 3060
        n, span = (80, 4) if busy else (20, 60)
        return [(offset + i * span / n, "KEKW" if busy else "hi") for i in range(n)]

    data = chat._via_gql_sampled("1", 7200, step=20, workers=4, page=page)
    assert data.coverage.all()
    assert data.rate[3010] > 10 * data.rate[100]


def test_fuse_ignores_uncovered_seconds():
    n = 1000
    audio = np.zeros(n)
    audio[800] = 0.5
    chat_sig = np.zeros(n)
    cov = np.zeros(n, dtype=bool)
    cov[:300] = True  # chat only covers the start
    chat_sig[100] = 0.5
    fused = signals.fuse({"audio": audio, "chat": chat_sig}, {"audio": 0.2, "chat": 0.25}, {"chat": cov})
    assert fused[800] == pytest.approx(0.5, rel=1e-6)  # not diluted by missing chat
    assert fused[100] < fused[800]


def test_ff_escape_quotes_windows_paths(tmp_path, monkeypatch):
    out = ff_escape(tmp_path / "a, b" / "x.ass")
    assert out.startswith("'") and out.endswith("'")
    with pytest.raises(ValueError):
        ff_escape(tmp_path / "o'brien" / "x.ass")
