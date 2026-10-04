"""Instagram Reels via the Graph API, uploading the local file directly (resumable upload),
so no public video hosting is needed.

Needs an Instagram professional account and a long-lived access token with
instagram_content_publish (IG_USER_ID + IG_ACCESS_TOKEN in .env).
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from ..http import session
from .base import AuthError, PostResult, Publisher, PublishError


class InstagramPublisher(Publisher):
    name = "instagram"

    def __init__(self, cfg):
        super().__init__(cfg)
        self.user_id = os.environ.get("IG_USER_ID", "")
        self.token = os.environ.get("IG_ACCESS_TOKEN", "")
        host = self.settings.get("api_host", "graph.facebook.com")
        self.base = f"https://{host}/{self.settings.get('api_version', 'v23.0')}"

    def ready(self) -> tuple[bool, str]:
        if not (self.user_id and self.token):
            return False, "set IG_USER_ID and IG_ACCESS_TOKEN in .env"
        return True, ""

    def _check(self, r) -> dict:
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code >= 400 or "error" in data:
            err = data.get("error") or {}
            msg = f"instagram HTTP {r.status_code}: {err or r.text[:300]}"
            if r.status_code == 401 or (isinstance(err, dict) and err.get("code") in (190, 102)):
                raise AuthError(msg + " — refresh IG_ACCESS_TOKEN (long-lived tokens last 60 days)")
            raise PublishError(msg)
        return data

    def publish(self, clip: dict, copy: dict, slot_key: str) -> PostResult:
        s = session()
        path = Path(clip["path"])
        size = path.stat().st_size
        container = self._check(s.post(f"{self.base}/{self.user_id}/media", data={
            "media_type": "REELS",
            "upload_type": "resumable",
            "caption": copy["instagram_caption"],
            "share_to_feed": "true" if self.settings.get("share_to_feed", True) else "false",
            "thumb_offset": str(int((clip.get("duration") or 10) * 400)),
            "access_token": self.token,
        }, timeout=60))
        container_id = container["id"]
        upload_uri = container.get("uri") or f"https://rupload.facebook.com/ig-api-upload/{self.settings.get('api_version', 'v23.0')}/{container_id}"

        with path.open("rb") as fh:
            self._check(s.post(upload_uri, data=fh, headers={
                "Authorization": f"OAuth {self.token}", "offset": "0", "file_size": str(size),
            }, timeout=600))

        deadline = time.time() + 600
        while True:
            status = self._check(s.get(f"{self.base}/{container_id}",
                                       params={"fields": "status_code,status", "access_token": self.token}, timeout=30))
            code = status.get("status_code")
            if code == "FINISHED":
                break
            if code in ("ERROR", "EXPIRED"):
                raise PublishError(f"instagram processing failed: {status.get('status')}")
            if time.time() > deadline:
                raise PublishError("instagram processing timed out")
            time.sleep(10)

        published = self._check(s.post(f"{self.base}/{self.user_id}/media_publish",
                                       data={"creation_id": container_id, "access_token": self.token}, timeout=60))
        media_id = published["id"]
        link = None
        try:
            link = self._check(s.get(f"{self.base}/{media_id}",
                                     params={"fields": "permalink", "access_token": self.token}, timeout=30)).get("permalink")
        except PublishError:
            pass
        return PostResult(remote_id=media_id, url=link)
