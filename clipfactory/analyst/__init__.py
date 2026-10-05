"""🧠 Analyst — turns a 6-hour VOD into a handful of judged, trimmed moments."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .. import fetch, log
from ..config import Config
from ..db import DB, loads
from ..llm import Claude, LLMError
from . import audio, chat, judge, signals, transcript

logger = log.get("analyst")


def _notes(peak: signals.Peak, sigs: dict[str, np.ndarray], clips: list[dict], gs: float, ge: float) -> list[str]:
    notes = [f"fused score {peak.score:.2f}"]
    inside = [c for c in clips if c.get("offset") is not None and gs - 10 <= c["offset"] <= ge]
    if inside:
        top = max(inside, key=lambda c: c.get("views") or 0)
        notes.append(f"{len(inside)} community clip(s) here, top: \"{top.get('title', '')}\" ({top.get('views', 0)} views)")
    s, e = int(gs), int(ge)
    for name in ("heatmap", "chat", "audio"):
        sig = sigs.get(name)
        if sig is not None and len(sig) > s:
            val = float(sig[s:e].max()) if e > s else 0.0
            if val > 0.25:
                notes.append(f"{name} spike {val:.2f}")
    return notes


def analyze_source(cfg: Config, db: DB, claude: Claude, source: dict) -> list[int]:
    """Analyze one source end-to-end. Returns ids of moments selected for editing."""
    a = cfg["analysis"]
    work = cfg.path("work") / f"src_{source['id']}"
    work.mkdir(parents=True, exist_ok=True)
    opts = fetch.ytdlp_opts(cfg)
    meta = loads(source.get("meta"), {})
    sid = source["id"]
    logger.info("analyzing %s — %s", source["creator"], source.get("title"))

    try:
        info = fetch.probe(source["url"], opts)
    except Exception as e:  # often transient ("try again later", rate limits): retry on later ticks
        attempts = int(db.kv_get(f"probe_attempts:{sid}", "0")) + 1
        db.kv_set(f"probe_attempts:{sid}", str(attempts))
        logger.warning("could not open %s (attempt %d/3): %s", source["url"], attempts, e)
        if attempts >= 3:
            db.update("sources", sid, status="failed", error=f"probe: {e}"[:500])
        return []
    if info.get("live_status") in ("is_live", "is_upcoming", "post_live"):
        logger.info("still live/processing — will retry later")
        return []
    duration = float(info.get("duration") or source.get("duration") or 0)
    d = cfg["discovery"]
    if not (d["min_source_minutes"] * 60 <= duration <= d["max_source_hours"] * 3600):
        db.update("sources", sid, status="skipped", error=f"duration {duration:.0f}s out of range", duration=duration)
        return []
    db.update("sources", sid, duration=duration, title=info.get("title") or source.get("title"))
    n = int(duration)

    # ---- crowd + audio signals over the whole thing
    sigs: dict[str, np.ndarray] = {}
    clips = meta.get("community_clips", [])
    if clips:
        sigs["community_clips"] = signals.clips_signal(clips, n)
    if info.get("heatmap"):
        sigs["heatmap"] = signals.heatmap_signal(info["heatmap"], n)
    messages: list[tuple[float, str]] = []
    coverage: dict[str, np.ndarray] = {}
    if source["platform"] == "twitch" and a["chat_replay"] != "off":
        chat_data = chat.fetch(source["external_id"], n, work, a["chat_replay"])
        if chat_data:
            messages = chat_data.messages
            delay = int(a["chat_delay_seconds"])
            sigs["chat"] = signals.chat_signal(chat_data.rate, delay)
            coverage["chat"] = np.concatenate([chat_data.coverage[delay:], np.zeros(delay, dtype=bool)])
    try:
        audio_path = fetch.download_audio(source["url"], work / "audio", opts)
        sigs["audio"] = signals.audio_signal(audio.loudness_per_second(audio_path)[:n])
        audio_path.unlink(missing_ok=True)
    except Exception as e:
        logger.warning("audio analysis failed: %s", e)

    if not sigs:
        db.update("sources", sid, status="failed", error="no signals available")
        return []
    score = signals.fuse(sigs, a["signal_weights"], coverage)
    peaks = signals.pick_peaks(
        score, a["candidates_per_source"], a["window_seconds"],
        skip_start=a["skip_stream_start_seconds"] if source.get("kind") == "vod" else 0,
        mask=signals.game_mask(clips, n),
    )
    logger.info("signals %s → %d candidate windows", sorted(sigs), len(peaks))
    if not peaks:
        db.update("sources", sid, status="skipped", error="no signal peaks")
        return []

    # ---- pull each window, transcribe it, grab frames
    candidates: list[judge.Candidate] = []
    for i, p in enumerate(peaks):
        gs, ge = max(0.0, p.start - 3), min(float(n), p.end + 3)
        try:
            local = fetch.download_section(source["url"], gs, ge, work / f"cand_{i}", opts, cfg["download"]["max_height"])
            local_dur = fetch.media_duration(local)
            words = transcript.transcribe(local, a["whisper_model"], a.get("language"))
        except Exception as e:
            logger.warning("candidate %d (%.0fs) failed: %s", i, p.t, e)
            continue
        frame_times, frames = [], []
        k = int(a.get("judge_frames") or 0)
        for j in range(k):
            t = local_dur * (j + 0.5) / k
            try:
                frames.append(fetch.extract_frame(local, t, work / f"cand_{i}_f{j}.jpg", width=512))
                frame_times.append(t)
            except Exception:
                pass
        notes = _notes(p, sigs, clips, gs, ge)
        mid = db.add_moment(sid, gs, ge, {"notes": notes, "peak": p.t, "score": p.score})
        db.update("moments", mid, local_path=str(local), local_offset=gs, words=words)
        candidates.append(judge.Candidate(
            id=mid, global_start=gs, global_end=ge, local_duration=local_dur, words=words,
            frames=frames, frame_times=frame_times, signal_notes=notes,
            chat=chat.reaction_summary(messages, gs, ge) if messages else None,
        ))
    if not candidates:
        db.update("sources", sid, status="failed", error="no candidate downloads succeeded")
        return []

    # ---- the judge decides
    try:
        verdicts = judge.judge(claude, cfg, source, candidates, db.recent_feedback())
    except LLMError as e:
        attempts = int(db.kv_get(f"judge_attempts:{sid}", "0")) + 1
        db.kv_set(f"judge_attempts:{sid}", str(attempts))
        logger.warning("judge failed (attempt %d/3): %s", attempts, e)
        if attempts >= 3:
            db.update("sources", sid, status="failed", error=f"judge: {e}")
        return []  # otherwise leave source as 'new' so a later tick retries

    keepers = []
    for v in verdicts:
        status = "selected" if v["keep"] and v["virality_score"] >= a["min_virality_score"] else "rejected"
        db.update("moments", v["candidate_id"], clip_start=v["start"], clip_end=v["end"], score=v["virality_score"],
                  category=v["category"], hook=v["hook_text"], title=v["title"], summary=v["what_happens"],
                  reason=v["reason"], status=status,
                  emphasis={"words": v.get("emphasis_words") or [], "hook": v.get("hook_emphasis") or None},
                  error=",".join(v["content_flags"]) or None)
        if status == "selected":
            keepers.append(v)
    keepers.sort(key=lambda v: v["virality_score"], reverse=True)
    selected = [v["candidate_id"] for v in keepers[: a["max_clips_per_source"]]]
    for v in keepers[a["max_clips_per_source"]:]:
        db.update("moments", v["candidate_id"], status="rejected", reason=(v["reason"] or "") + " [per-source cap]")
    for c in candidates:
        if c.id not in selected:
            row = db.one("SELECT local_path FROM moments WHERE id = ?", [c.id])
            if row and row["local_path"]:
                _unlink(row["local_path"])
        for f in c.frames:
            _unlink(str(f))
    db.update("sources", sid, status="analyzed")
    logger.info("%s: %d/%d windows made the cut (best %s)", source["creator"], len(selected), len(candidates),
                keepers[0]["virality_score"] if keepers else "-")
    return selected


def _unlink(path: str) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass
