"""Twitch Helix client: VODs, community clips (the crowd's own highlight picks), categories."""

from __future__ import annotations

import os
import re
import time
from datetime import datetime, timedelta, timezone

from ..http import session

HELIX = "https://api.twitch.tv/helix"


def parse_duration(value: str) -> int:
    """'3h2m1s' -> 10921"""
    total = 0
    for amount, unit in re.findall(r"(\d+)([hms])", value or ""):
        total += int(amount) * {"h": 3600, "m": 60, "s": 1}[unit]
    return total


def rfc3339(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Twitch:
    def __init__(self, client_id: str | None = None, client_secret: str | None = None):
        self.client_id = client_id or os.environ.get("TWITCH_CLIENT_ID", "")
        self.client_secret = client_secret or os.environ.get("TWITCH_CLIENT_SECRET", "")
        self._token: str | None = None
        self._token_exp = 0.0

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def _auth(self) -> str:
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        r = session().post(
            "https://id.twitch.tv/oauth2/token",
            params={"client_id": self.client_id, "client_secret": self.client_secret, "grant_type": "client_credentials"},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        self._token = data["access_token"]
        self._token_exp = time.time() + data.get("expires_in", 3600)
        return self._token

    def get(self, path: str, params: dict | list) -> dict:
        for attempt in range(2):
            r = session().get(
                f"{HELIX}/{path}",
                params=params,
                headers={"Client-Id": self.client_id, "Authorization": f"Bearer {self._auth()}"},
                timeout=20,
            )
            if r.status_code == 401 and attempt == 0:
                self._token = None
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError("twitch auth failed")

    def paged(self, path: str, params: dict, max_pages: int = 3) -> list[dict]:
        items, cursor = [], None
        for _ in range(max_pages):
            p = dict(params)
            if cursor:
                p["after"] = cursor
            data = self.get(path, p)
            items.extend(data.get("data", []))
            cursor = data.get("pagination", {}).get("cursor")
            if not cursor:
                break
        return items

    # ------------------------------------------------------------------ endpoints
    def users(self, logins: list[str]) -> dict[str, dict]:
        out = {}
        for i in range(0, len(logins), 100):
            data = self.get("users", [("login", login.lower()) for login in logins[i : i + 100]])
            for u in data.get("data", []):
                out[u["login"].lower()] = u
        return out

    def live_streams(self, user_ids: list[str]) -> dict[str, dict]:
        if not user_ids:
            return {}
        data = self.get("streams", [("user_id", uid) for uid in user_ids[:100]])
        return {s["user_id"]: s for s in data.get("data", [])}

    def vods(self, user_id: str, first: int = 15) -> list[dict]:
        return self.get("videos", {"user_id": user_id, "type": "archive", "first": first}).get("data", [])

    def videos_by_id(self, ids: list[str]) -> list[dict]:
        out = []
        for i in range(0, len(ids), 100):
            out.extend(self.get("videos", [("id", v) for v in ids[i : i + 100]]).get("data", []))
        return out

    def clips(
        self,
        broadcaster_id: str | None = None,
        game_id: str | None = None,
        started_at: datetime | None = None,
        ended_at: datetime | None = None,
        max_pages: int = 3,
    ) -> list[dict]:
        params: dict = {"first": 100}
        if broadcaster_id:
            params["broadcaster_id"] = broadcaster_id
        if game_id:
            params["game_id"] = game_id
        if started_at:
            params["started_at"] = rfc3339(started_at)
            params["ended_at"] = rfc3339(ended_at or started_at + timedelta(days=7))
        return self.paged("clips", params, max_pages=max_pages)

    def game_ids(self, names: list[str]) -> dict[str, str]:
        data = self.get("games", [("name", n) for n in names])
        return {g["name"]: g["id"] for g in data.get("data", [])}


def clip_summary(clip: dict) -> dict:
    """Trim a Helix clip down to what the Analyst needs."""
    return {
        "offset": clip.get("vod_offset"),
        "duration": clip.get("duration", 30),
        "views": clip.get("view_count", 0),
        "title": clip.get("title", ""),
        "game_id": clip.get("game_id"),
        "url": clip.get("url"),
    }
