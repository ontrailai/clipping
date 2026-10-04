"""Daily posting slots. A slot is due from its time until midnight; one post per tick,
with a minimum gap so a machine that was asleep catches up without spamming."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from .db import DB


def post_times(cfg) -> list[str]:
    n = int(cfg["clips_per_day"])
    times = list(cfg["post_times"])
    if len(times) >= n:
        return times[:n]
    # not enough configured times: spread the day 10:00–22:00 evenly
    step = 12 * 60 / max(1, n - 1) if n > 1 else 0
    return [f"{int(10 * 60 + i * step) // 60:02d}:{int(10 * 60 + i * step) % 60:02d}" for i in range(n)]


def slots_for(cfg, day: date) -> list[tuple[str, datetime]]:
    out = []
    for hhmm in post_times(cfg):
        h, m = (int(x) for x in hhmm.split(":"))
        local = datetime.combine(day, time(h, m), tzinfo=cfg.tz)
        out.append((local.strftime("%Y-%m-%dT%H:%M"), local))
    return sorted(out, key=lambda s: s[1])


def due_slot(cfg, db: DB, now: datetime | None = None) -> str | None:
    now = now or datetime.now(timezone.utc)
    local_now = now.astimezone(cfg.tz)
    last = db.last_post_time()
    if last and now - last < timedelta(minutes=cfg["min_minutes_between_posts"]):
        return None
    for key, at in slots_for(cfg, local_now.date()):
        if at <= local_now and not db.slot_filled(key):
            return key
    return None


def remaining_today(cfg, db: DB, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    return sum(1 for key, _ in slots_for(cfg, now.astimezone(cfg.tz).date()) if not db.slot_filled(key))


def next_slot(cfg, db: DB, now: datetime | None = None) -> tuple[str, datetime] | None:
    now = now or datetime.now(timezone.utc)
    local_now = now.astimezone(cfg.tz)
    for day in (local_now.date(), local_now.date() + timedelta(days=1)):
        for key, at in slots_for(cfg, day):
            if not db.slot_filled(key) and (at > local_now or day == local_now.date()):
                return key, at
    return None
