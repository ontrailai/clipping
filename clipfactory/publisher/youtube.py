"""YouTube Shorts via the Data API v3 (vertical + under 60s = a Short).

Setup: Google Cloud project → enable "YouTube Data API v3" → OAuth client (Desktop app) →
download JSON to secrets/youtube_client_secret.json → `clipfactory auth youtube`.
Note: projects that haven't passed Google's API audit get uploads locked to private, and
the default quota (10k units/day, 1,600 per upload) allows ~6 uploads a day — enough for 3.
"""

from __future__ import annotations

import os
from pathlib import Path

from .base import PostResult, Publisher, PublishError

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def _token_path(cfg) -> Path:
    return cfg.home / "secrets" / "youtube_token.json"


def authorize(cfg) -> Path:
    from google_auth_oauthlib.flow import InstalledAppFlow

    secrets = cfg.home / os.environ.get("YOUTUBE_CLIENT_SECRETS", "secrets/youtube_client_secret.json")
    if not secrets.exists():
        raise PublishError(f"OAuth client file not found: {secrets}")
    flow = InstalledAppFlow.from_client_secrets_file(str(secrets), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    path = _token_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(creds.to_json())
    return path


class YouTubePublisher(Publisher):
    name = "youtube"

    def ready(self) -> tuple[bool, str]:
        try:
            import googleapiclient  # noqa: F401
        except ImportError:
            return False, "pip install 'clipfactory[youtube]'"
        if not _token_path(self.cfg).exists():
            return False, "run `clipfactory auth youtube`"
        return True, ""

    def _service(self):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        path = _token_path(self.cfg)
        creds = Credentials.from_authorized_user_file(str(path), SCOPES)
        if not creds.valid and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            path.write_text(creds.to_json())
        return build("youtube", "v3", credentials=creds, cache_discovery=False)

    def publish(self, clip: dict, copy: dict, slot_key: str) -> PostResult:
        from googleapiclient.http import MediaFileUpload

        body = {
            "snippet": {
                "title": copy["youtube_title"],
                "description": copy["youtube_description"],
                "tags": copy.get("youtube_tags", []),
                "categoryId": str(self.settings.get("category_id", "20")),
            },
            "status": {"privacyStatus": self.settings.get("privacy", "public"), "selfDeclaredMadeForKids": False},
        }
        media = MediaFileUpload(clip["path"], mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
        request = self._service().videos().insert(part="snippet,status", body=body, media_body=media)
        response = None
        while response is None:
            _status, response = request.next_chunk()
        video_id = response["id"]
        return PostResult(remote_id=video_id, url=f"https://youtube.com/shorts/{video_id}")
