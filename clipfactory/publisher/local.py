"""Drops a ready-to-post folder (video, cover, captions) — the manual fallback for any platform."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .base import PostResult, Publisher


class LocalPublisher(Publisher):
    name = "local"

    def publish(self, clip: dict, copy: dict, slot_key: str) -> PostResult:
        base = Path(self.settings.get("dir", "out/ready"))
        base = base if base.is_absolute() else self.cfg.home / base
        folder = base / f"{slot_key.replace(':', '')}_{clip['creator'].replace(' ', '')}_{clip['id']}"
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copy2(clip["path"], folder / "video.mp4")
        if clip.get("cover_path") and Path(clip["cover_path"]).exists():
            shutil.copy2(clip["cover_path"], folder / "cover.jpg")
        (folder / "caption_instagram.txt").write_text(copy.get("instagram_caption", ""), encoding="utf-8")
        (folder / "caption_tiktok.txt").write_text(copy.get("tiktok_caption", ""), encoding="utf-8")
        (folder / "youtube.txt").write_text(f"{copy.get('youtube_title', '')}\n\n{copy.get('youtube_description', '')}", encoding="utf-8")
        (folder / "meta.json").write_text(json.dumps({"clip_id": clip["id"], "slot": slot_key, **copy}, indent=2), encoding="utf-8")
        return PostResult(remote_id=None, url=str(folder))
