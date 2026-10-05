"""Tuning tools: caption presets, keyword emphasis, re-render, gallery, ratings → judge prompt."""

import sqlite3

import pytest

from clipfactory import gallery
from clipfactory.analyst import judge
from clipfactory.db import DB, iso, loads
from clipfactory.editor import captions, presets, rerender
from clipfactory import fetch
from tests.conftest import fake_words, needs_ffmpeg
from tests.test_pipeline import FakeClaude, SPEECH, _cut, _tune


@pytest.mark.parametrize("name", list(presets.PRESETS))
def test_every_preset_builds_captions(name):
    ass = captions.build_ass(fake_words("he stole the cop car"), 3.0, presets.style_for(name),
                             captions.LAYOUT_POSITIONS["blur_fill"], hook="xQc steals a cop car",
                             emphasis_words=["cop car"], hook_emphasis="cop car", credit="@xqc • Kick")
    style = presets.style_for(name)
    assert f"Style: Caption,{style.font},{style.size}" in ass
    assert ass.count(",Caption,") == 5
    hook = next(line for line in ass.splitlines() if ",Hook," in line)
    assert captions.ass_color(style.hook_emphasis) in hook  # key phrase coloured


def test_emphasis_words_stay_coloured_and_box_mode():
    style = presets.style_for("boxed")
    ass = captions.build_ass(fake_words("he stole the cop car"), 3.0, style, captions.LAYOUT_POSITIONS["blur_fill"],
                             emphasis_words=["stole"])
    lines = [line for line in ass.splitlines() if ",Caption," in line]
    emph = captions.ass_color(style.emphasis)
    # while "he" is spoken, "STOLE" is already in the emphasis colour; the spoken word sits on a box
    assert emph in lines[0] and "\\bord15" in lines[0]


def test_style_overrides_and_unknown_preset():
    s = presets.style_for("punchy", {"highlight": "#00E5FF", "size": 99, "not_a_field": 1})
    assert s.highlight == "#00E5FF" and s.size == 99
    with pytest.raises(ValueError):
        presets.style_for("nope")


def test_db_migrates_old_schema(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE clips (id INTEGER PRIMARY KEY, moment_id INTEGER, creator TEXT, path TEXT, cover_path TEXT,"
                " duration REAL, score REAL, copy TEXT, qc TEXT, status TEXT, created_at TEXT, published_at TEXT)")
    con.commit()
    con.close()
    db = DB(path)
    cols = {r["name"] for r in db.all("PRAGMA table_info(clips)")}
    assert "render" in cols


def test_feedback_reaches_judge_prompt(cfg, db):
    sid = db.add_source("twitch", "1", "xQc", "t", "u", iso(), 9000, "vod", {})
    mid = db.add_moment(sid, 0, 60, {})
    db.update("moments", mid, hook="XQC GETS BETRAYED", category="betrayal", score=82)
    cid = db.add_clip(mid, "xQc", "/x.mp4", None, 31, 82)
    db.rate(cid, "bad", "too slow at the start")
    notes = judge.taste_notes(cfg, db.recent_feedback())
    assert "House style" in notes  # config/style.example.md is picked up
    assert "DISLIKED" in notes and "too slow at the start" in notes and "XQC GETS BETRAYED" in notes


@needs_ffmpeg
def test_rerender_and_gallery(cfg, db, sample_video, monkeypatch):
    from clipfactory.analyst import transcript
    from clipfactory.producer import Producer

    _tune(cfg)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    heat = [{"start_time": i, "end_time": i + 1, "value": 1.0 if 20 <= i < 24 else 0.1} for i in range(40)]
    monkeypatch.setattr(fetch, "probe", lambda url, opts=None, flat=False: {"duration": 40, "heatmap": heat})
    monkeypatch.setattr(fetch, "download_audio", lambda url, out_base, opts=None: _cut(sample_video, 0, 40, out_base.with_suffix(".mp4")))
    monkeypatch.setattr(fetch, "download_section",
                        lambda url, s, e, out_base, opts=None, max_height=1080: _cut(sample_video, s, e, out_base.with_suffix(".mp4")))
    monkeypatch.setattr(transcript, "transcribe", lambda path, size="small", language="en": fake_words(SPEECH, 0.5, 0.45))
    db.add_source("youtube", "vid9", "xQc", "GTA 6", "https://www.youtube.com/watch?v=vid9", iso(), 40, "video", {})
    p = Producer(cfg, db, FakeClaude())
    assert p.produce(1) == 1
    clip = db.clips("approved")[0]
    params = loads(clip["render"])
    assert params["preset"] == "punchy" and params["layout"] in ("blur_fill", "facecam_split")

    # re-trim + new preset + new hook, without re-analysing
    rerender(cfg, db, clip["id"], preset="boxed", hook="NEW HOOK", start=params["start"] + 1, end=params["end"] - 1)
    updated = db.clip(clip["id"])
    assert loads(updated["render"])["preset"] == "boxed"
    assert updated["duration"] == pytest.approx(clip["duration"] - 2, abs=0.2)
    assert db.one("SELECT hook FROM moments WHERE id = ?", [clip["moment_id"]])["hook"] == "NEW HOOK"

    db.rate(clip["id"], "good", "great pacing")
    page = gallery.build(db, cfg.path("out"))
    text = page.read_text()
    assert "NEW HOOK" in text and "great pacing" in text and "clips/" in text and "Thrown out by the judge" in text


def test_source_links():
    assert gallery.source_link({"platform": "youtube", "url": "https://www.youtube.com/watch?v=a"}, 75) == "https://www.youtube.com/watch?v=a&t=75s"
    assert gallery.source_link({"platform": "twitch", "url": "https://www.twitch.tv/videos/1"}, 3725) == "https://www.twitch.tv/videos/1?t=1h2m5s"
