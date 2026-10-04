from datetime import datetime
from zoneinfo import ZoneInfo

from clipfactory import scheduler

NY = ZoneInfo("America/New_York")


def at(h, m=0, day=19):
    return datetime(2026, 11, day, h, m, tzinfo=NY)


def test_nothing_due_before_first_slot(cfg, db):
    assert scheduler.due_slot(cfg, db, at(9)) is None
    assert scheduler.remaining_today(cfg, db, at(9)) == 3


def test_due_slot_and_fill(cfg, db):
    assert scheduler.due_slot(cfg, db, at(12, 5)) == "2026-11-19T12:00"
    db.fill_slot("2026-11-19T12:00", 1)
    assert scheduler.due_slot(cfg, db, at(12, 6)) is None
    assert scheduler.remaining_today(cfg, db, at(12, 6)) == 2


def test_catch_up_respects_min_gap(cfg, db, monkeypatch):
    # machine slept all day: first catch-up post goes out, the next waits for the gap
    now = at(21, 30)
    assert scheduler.due_slot(cfg, db, now) == "2026-11-19T12:00"
    db.fill_slot("2026-11-19T12:00", 1)
    monkeypatch.setattr(db, "last_post_time", lambda: now)
    assert scheduler.due_slot(cfg, db, now) is None


def test_post_times_spread_when_more_clips_than_times(cfg):
    cfg.raw["clips_per_day"] = 5
    cfg.raw["post_times"] = ["12:00"]
    times = scheduler.post_times(cfg)
    assert times[0] == "10:00" and times[-1] == "22:00" and len(times) == 5
