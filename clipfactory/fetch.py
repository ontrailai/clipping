"""Download helpers: yt-dlp for anything on YouTube / Twitch / Kick, ffmpeg/ffprobe for local media."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yt_dlp
from yt_dlp.utils import download_range_func

from . import log

logger = log.get("analyst")


def ytdlp_opts(cfg) -> dict:
    d = cfg["download"]
    opts: dict[str, Any] = {"quiet": True, "no_warnings": True, "noprogress": True, "retries": 5, "fragment_retries": 10}
    if d.get("cookies_from_browser"):
        opts["cookiesfrombrowser"] = (d["cookies_from_browser"],)
    if d.get("cookiefile"):
        opts["cookiefile"] = d["cookiefile"]
    return opts


def probe(url: str, opts: dict | None = None, flat: bool = False) -> dict:
    o = dict(opts or {})
    o["skip_download"] = True
    if flat:
        o["extract_flat"] = "in_playlist"
        o["playlistend"] = 5
    with yt_dlp.YoutubeDL(o) as ydl:
        return ydl.sanitize_info(ydl.extract_info(url, download=False))


def _downloaded_path(base: Path) -> Path:
    matches = sorted(base.parent.glob(base.name + ".*"), key=lambda p: p.stat().st_size, reverse=True)
    matches = [m for m in matches if m.suffix not in (".part", ".ytdl", ".json")]
    if not matches:
        raise RuntimeError(f"download produced no file for {base}")
    return matches[0]


def download_section(url: str, start: float, end: float, out_base: Path, opts: dict | None = None, max_height: int = 1080) -> Path:
    """Download only [start, end] seconds of a video. Returns the media path."""
    o = dict(opts or {})
    o.update(
        {
            "format": f"bestvideo[height<={max_height}]+bestaudio/best[height<={max_height}]/best",
            "outtmpl": str(out_base) + ".%(ext)s",
            "merge_output_format": "mp4",
            "download_ranges": download_range_func(None, [(max(0.0, start), end)]),
            "force_keyframes_at_cuts": False,
            "overwrites": True,
        }
    )
    out_base.parent.mkdir(parents=True, exist_ok=True)
    with yt_dlp.YoutubeDL(o) as ydl:
        ydl.download([url])
    return _downloaded_path(out_base)


def download_audio(url: str, out_base: Path, opts: dict | None = None) -> Path:
    """Lowest-bitrate audio track (enough for loudness analysis of a multi-hour VOD)."""
    o = dict(opts or {})
    o.update({"format": "worstaudio/bestaudio/worst", "outtmpl": str(out_base) + ".%(ext)s", "overwrites": True})
    out_base.parent.mkdir(parents=True, exist_ok=True)
    with yt_dlp.YoutubeDL(o) as ydl:
        ydl.download([url])
    return _downloaded_path(out_base)


def require_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise RuntimeError(f"{tool} not found on PATH — install ffmpeg first")


def ffprobe(path: str | Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(out.stdout)


def media_duration(path: str | Path) -> float:
    return float(ffprobe(path)["format"]["duration"])


def video_size(path: str | Path) -> tuple[int, int]:
    for s in ffprobe(path)["streams"]:
        if s.get("codec_type") == "video":
            return int(s["width"]), int(s["height"])
    raise RuntimeError(f"no video stream in {path}")


def extract_frame(path: str | Path, t: float, out: Path, width: int = 640) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(path), "-frames:v", "1",
         "-vf", f"scale={width}:-2", "-q:v", "4", str(out)],
        check=True,
    )
    return out


def run_ffmpeg(args: list[str]) -> None:
    proc = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-v", "error", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr.strip()[-2000:]}")
