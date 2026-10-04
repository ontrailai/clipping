from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PostResult:
    remote_id: str | None
    url: str | None


class PublishError(RuntimeError):
    pass


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
