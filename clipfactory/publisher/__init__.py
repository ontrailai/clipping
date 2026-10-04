"""📤 Publisher — every enabled platform gets the clip; failures are recorded and retried."""

from __future__ import annotations

from .base import AuthError, PostResult, Publisher, PublishError, PublishPending
from .instagram import InstagramPublisher
from .local import LocalPublisher
from .tiktok import TikTokPublisher
from .youtube import YouTubePublisher

REGISTRY: dict[str, type[Publisher]] = {
    "local": LocalPublisher,
    "youtube": YouTubePublisher,
    "instagram": InstagramPublisher,
    "tiktok": TikTokPublisher,
}


def enabled(cfg) -> list[Publisher]:
    return [cls(cfg) for name, cls in REGISTRY.items() if cfg["platforms"].get(name, {}).get("enabled")]


__all__ = ["AuthError", "PostResult", "Publisher", "PublishError", "PublishPending", "REGISTRY", "enabled"]
