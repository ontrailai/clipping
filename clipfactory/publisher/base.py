from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PostResult:
    remote_id: str | None
    url: str | None


class PublishError(RuntimeError):
    pass


class AuthError(PublishError):
    """Login/token problem: fix the credentials; not the clip's fault."""


class PublishPending(PublishError):
    """Upload finished but the platform is still processing; check again later with check()."""

    def __init__(self, remote_id: str, message: str = "still processing"):
        super().__init__(message)
        self.remote_id = remote_id


class Publisher:
    name = "base"

    def __init__(self, cfg):
        self.cfg = cfg
        self.settings = cfg["platforms"].get(self.name, {})

    def ready(self) -> tuple[bool, str]:
        """(is configured, reason if not)"""
        return True, ""

    def publish(self, clip: dict, copy: dict, slot_key: str) -> PostResult:
        raise NotImplementedError

    def check(self, remote_id: str) -> PostResult:
        """Resume a PublishPending upload."""
        raise PublishError(f"{self.name} cannot resume uploads")
