"""✂️ Editor — cuts the moment, reframes it to 9:16, burns in captions, hook and credit."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .. import fetch, log
from ..config import Config
from ..db import DB, loads
from ..llm import Claude
from . import captions, facecam, layout, presets

logger = log.get("editor")

PLATFORM_NAMES = {"twitch": "Twitch", "kick": "Kick", "youtube": "YouTube"}


def caption_style(cfg: Config, preset: str | None = None) -> captions.Style:
    e = cfg["edit"]
    return presets.style_for(preset or e.get("preset", "punchy"), e.get("caption_overrides"))


def render(
    cfg: Config,
    src: Path,
    start: float,
    end: float,
    words: list[dict],
    out: Path,
    hook: str | None,
    credit: str | None,
    layout_name: str = "blur_fill",
    facecam_box: dict | None = None,
    preset: str | None = None,
    emphasis_words: list[str] | None = None,
    hook_emphasis: str | None = None,
) -> Path:
    """Render one vertical clip from a local file. `words` are on the src timeline."""
    fetch.require_ffmpeg()
    e = cfg["edit"]
    duration = round(end - start, 3)
    if layout_name == "facecam_split" and not facecam_box:
        layout_name = "blur_fill"
    clip_words = [
        {"w": w["w"], "s": round(max(0.0, w["s"] - start), 3), "e": round(min(end, w["e"]) - start, 3)}
        for w in words if w["e"] > start + 0.05 and w["s"] < end - 0.05
    ]
    out.parent.mkdir(parents=True, exist_ok=True)
    ass_path = out.with_suffix(".ass")
    ass_path.write_text(
        captions.build_ass(
            clip_words, duration, caption_style(cfg, preset), captions.LAYOUT_POSITIONS[layout_name],
            hook=hook, hook_seconds=e["hook_seconds"], credit=credit if e["credit"] else None,
            emphasis_words=emphasis_words, hook_emphasis=hook_emphasis,
        ),
        encoding="utf-8",
    )
    fg = layout.graph(layout_name, ass_path, cfg.fonts_dir, e["fps"], e["gameplay_zoom"], facecam_box, e["target_lufs"])
    fetch.run_ffmpeg([
        "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(src),
        "-filter_complex", fg, "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "medium", "-crf", str(e["crf"]), "-profile:v", "high",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
        "-movflags", "+faststart", "-shortest", str(out),
    ])
    return out


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:40] or "clip"


def render_moment(cfg: Config, db: DB, claude: Claude | None, moment: dict) -> int | None:
    source = db.one("SELECT * FROM sources WHERE id = ?", [moment["source_id"]])
    local = Path(moment["local_path"] or "")
    if not source or not local.exists():
        db.update("moments", moment["id"], status="failed", error="local media missing")
        return None
    creator = cfg.creator(source["creator"])
    words = loads(moment["words"], [])

    e = cfg["edit"]
    layout_name = e["layout"]
    box = None
    if layout_name in ("auto", "facecam_split"):
        frames = []
        if e["detect_facecam"] or (creator and creator.facecam):
            dur = fetch.media_duration(local)
            for i, t in enumerate((dur * 0.25, dur * 0.6)):
                try:
                    frames.append(fetch.extract_frame(local, t, local.with_name(f"fc_{moment['id']}_{i}.jpg"), 768))
                except Exception:
                    pass
        box = facecam.facecam_for(db, claude if e["detect_facecam"] else None, source["creator"], source["platform"],
                                  frames, creator.facecam if creator else None)
        for f in frames:
            f.unlink(missing_ok=True)
        layout_name = "facecam_split" if box else "blur_fill"

    credit_name = creator.credit_handle if creator else source["creator"]
    credit = f"{credit_name} • {PLATFORM_NAMES.get(source['platform'], source['platform'])}"
    stamp = datetime.now().strftime("%Y%m%d")
    out = cfg.path("out") / "clips" / f"{stamp}_{_slug(source['creator'])}_{moment['id']}_{_slug(moment['title'])}.mp4"
    emphasis = loads(moment.get("emphasis"), {}) or {}
    params = {
        "layout": layout_name, "facecam": box, "preset": e.get("preset", "punchy"),
        "start": moment["clip_start"], "end": moment["clip_end"], "hook": moment["hook"], "credit": credit,
        "emphasis_words": emphasis.get("words") or [], "hook_emphasis": emphasis.get("hook"),
    }

    logger.info("rendering #%s %s (%s, %.1fs)", moment["id"], moment["title"], layout_name,
                moment["clip_end"] - moment["clip_start"])
    try:
        _render_params(cfg, local, words, out, params)
        cover = fetch.extract_frame(out, (moment["clip_end"] - moment["clip_start"]) * 0.4, out.with_suffix(".jpg"), 1080)
    except Exception as ex:
        db.update("moments", moment["id"], status="failed", error=str(ex)[:500])
        logger.warning("render failed: %s", ex)
        return None

    clip_id = db.add_clip(moment["id"], source["creator"], str(out), str(cover), fetch.media_duration(out), moment["score"] or 0)
    db.update("clips", clip_id, render=params)
    db.update("moments", moment["id"], status="rendered")
    if not e.get("keep_source", True):
        local.unlink(missing_ok=True)
    return clip_id


def _render_params(cfg: Config, local: Path, words: list[dict], out: Path, p: dict) -> Path:
    return render(cfg, local, p["start"], p["end"], words, out, p["hook"], p["credit"], p["layout"], p["facecam"],
                  p["preset"], p.get("emphasis_words"), p.get("hook_emphasis"))


def rerender(cfg: Config, db: DB, clip_id: int, out: Path | None = None, **changes) -> Path:
    """Re-render an existing clip with tweaks — preset, start/end (seconds on the clip's source
    window), hook, hook_emphasis, layout — without re-analysing anything. Saves the new params."""
    clip = db.clip(clip_id)
    if not clip:
        raise ValueError(f"no clip #{clip_id}")
    moment = db.one("SELECT * FROM moments WHERE id = ?", [clip["moment_id"]])
    local = Path(moment["local_path"] or "")
    if not local.exists():
        raise RuntimeError("source footage for this clip was cleaned up — re-run `clipfactory clip` on the video")
    params = loads(clip.get("render"), {}) or {}
    for k, v in changes.items():
        if v is not None:
            params[k] = v
    if params.get("preset"):
        presets.style_for(params["preset"])  # validate early
    target = out or Path(clip["path"])
    _render_params(cfg, local, loads(moment["words"], []), target, params)
    if out is None:
        fetch.extract_frame(target, (params["end"] - params["start"]) * 0.4, target.with_suffix(".jpg"), 1080)
        db.update("clips", clip_id, render=params, duration=fetch.media_duration(target))
        db.update("moments", moment["id"], clip_start=params["start"], clip_end=params["end"], hook=params["hook"])
    return target
