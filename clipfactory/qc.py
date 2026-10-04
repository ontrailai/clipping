"""🛡️ QC — nothing goes out unless the file is technically right and the words are safe and true."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from . import fetch
from .llm import Claude

SYSTEM = """You are the final quality gate for a GTA clip page before a video is auto-posted to \
TikTok, Instagram and YouTube. Approve only if ALL are true:
- The on-screen hook and caption are accurate to what is said/happens (no misleading clickbait).
- No slurs, hate speech, sexual content, harassment, real-world violence or personal info in the \
transcript, hook or caption.
- The creator is credited.
- The text reads naturally (no garbled words, no broken sentences, no prompt artifacts).
List concrete issues if you reject."""

SCHEMA = {
    "type": "object",
    "properties": {
        "approve": {"type": "boolean"},
        "issues": {"type": "array", "items": {"type": "string"}},
    },
}


def mean_volume(path: str | Path) -> float | None:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-vn", "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", proc.stderr)
    return float(m.group(1)) if m else None


def technical(path: str | Path, cfg) -> list[str]:
    a = cfg["analysis"]
    issues = []
    p = Path(path)
    if not p.exists() or p.stat().st_size < 50_000:
        return ["file missing or too small"]
    info = fetch.ffprobe(p)
    v = next((s for s in info["streams"] if s.get("codec_type") == "video"), None)
    au = next((s for s in info["streams"] if s.get("codec_type") == "audio"), None)
    if not v or (int(v["width"]), int(v["height"])) != (1080, 1920):
        issues.append("video is not 1080x1920")
    if not au:
        issues.append("no audio track")
    dur = float(info["format"]["duration"])
    if not (a["min_clip_seconds"] - 1 <= dur <= a["max_clip_seconds"] + 1.5):
        issues.append(f"duration {dur:.1f}s outside {a['min_clip_seconds']}-{a['max_clip_seconds']}s")
    if p.stat().st_size > 250 * 1024 * 1024:
        issues.append("file larger than 250MB")
    vol = mean_volume(p) if au else None
    if vol is not None and vol < -45:
        issues.append(f"audio nearly silent ({vol:.0f} dB)")
    return issues


def textual(copy: dict, words: list[dict], cfg) -> list[str]:
    issues = []
    if cfg["qc"]["require_captions"] and len(words) < 4:
        issues.append("too little speech for captions")
    if copy.get("credit", "") not in copy.get("instagram_caption", ""):
        issues.append("credit line missing")
    if len(copy.get("youtube_title", "")) > 100:
        issues.append("youtube title too long")
    return issues


def llm_review(claude: Claude, cfg, moment: dict, copy: dict, transcript_text: str) -> list[str]:
    prompt = (
        f"On-screen hook: {moment.get('hook')}\n"
        f"What happens (editor's note): {moment.get('summary')}\n"
        f"Transcript: {transcript_text[:2500]}\n\n"
        f"Caption:\n{copy.get('instagram_caption')}\n\nYouTube title: {copy.get('youtube_title')}"
    )
    result = claude.json(SYSTEM, prompt, SCHEMA, effort=cfg["llm"]["effort_qc"], max_tokens=3000)
    return [] if result.get("approve") else (result.get("issues") or ["rejected by reviewer"])


def run(claude: Claude | None, cfg, clip: dict, moment: dict, copy: dict, words: list[dict], transcript_text: str) -> dict:
    issues = technical(clip["path"], cfg) + textual(copy, words, cfg)
    if not issues and claude is not None and cfg["qc"]["llm_review"]:
        issues += llm_review(claude, cfg, moment, copy, transcript_text)
    return {"passed": not issues, "issues": issues}
