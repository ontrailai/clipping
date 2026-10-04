"""TikTok via the Content Posting API.

mode: inbox  → lands in the account's TikTok drafts/inbox for a one-tap post (needs video.upload) — default
mode: direct → posts immediately (needs video.publish). Until TikTok audits your app, posts are
               forced to SELF_ONLY (private); set `audited: true` once the audit passes.

Run `clipfactory auth tiktok` once; tokens refresh themselves after that.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import secrets
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from ..http import session
from .base import AuthError, PostResult, Publisher, PublishError, PublishPending

API = "https://open.tiktokapis.com/v2"
SCOPES = "user.info.basic,video.publish,video.upload"
CHUNK = 10 * 1024 * 1024


def _token_path(cfg) -> Path:
    return cfg.home / "secrets" / "tiktok_token.json"


def _client() -> tuple[str, str, str]:
    key = os.environ.get("TIKTOK_CLIENT_KEY", "")
    secret = os.environ.get("TIKTOK_CLIENT_SECRET", "")
    redirect = os.environ.get("TIKTOK_REDIRECT_URI", "http://localhost:8765/callback/")
    if not (key and secret):
        raise PublishError("set TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET in .env")
    return key, secret, redirect


def _save(cfg, data: dict) -> None:
    data["obtained_at"] = time.time()
    path = _token_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def authorize(cfg) -> Path:
    key, secret, redirect = _client()
    state = secrets.token_urlsafe(16)
    # PKCE (required for TikTok desktop apps; TikTok wants the SHA256 challenge hex-encoded)
    verifier = secrets.token_urlsafe(64)[:64]
    challenge = hashlib.sha256(verifier.encode()).hexdigest()
    url = "https://www.tiktok.com/v2/auth/authorize/?" + urllib.parse.urlencode({
        "client_key": key, "scope": SCOPES, "response_type": "code", "redirect_uri": redirect, "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
    })
    code_holder: dict = {}
    parsed = urllib.parse.urlparse(redirect)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            code_holder.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"TikTok connected - you can close this tab.")

        def log_message(self, *args):
            pass

    print(f"Open this URL to connect TikTok:\n{url}\n")
    if parsed.hostname in ("localhost", "127.0.0.1"):
        webbrowser.open(url)
        HTTPServer((parsed.hostname, parsed.port or 80), Handler).handle_request()
    else:
        pasted = input("After approving, paste the full URL you were redirected to: ").strip()
        code_holder.update({k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(pasted).query).items()})
    if code_holder.get("state") != state or "code" not in code_holder:
        raise PublishError(f"TikTok authorization failed: {code_holder}")
    r = session().post(f"{API}/oauth/token/", data={
        "client_key": key, "client_secret": secret, "code": code_holder["code"],
        "grant_type": "authorization_code", "redirect_uri": redirect, "code_verifier": verifier,
    }, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30)
    data = r.json()
    if "access_token" not in data:
        raise PublishError(f"TikTok token exchange failed: {data}")
    _save(cfg, data)
    return _token_path(cfg)


class TikTokPublisher(Publisher):
    name = "tiktok"

    def ready(self) -> tuple[bool, str]:
        if not _token_path(self.cfg).exists():
            return False, "run `clipfactory auth tiktok`"
        return True, ""

    def _token(self) -> str:
        path = _token_path(self.cfg)
        data = json.loads(path.read_text())
        if time.time() > data.get("obtained_at", 0) + data.get("expires_in", 86400) - 600:
            key, secret, _ = _client()
            r = session().post(f"{API}/oauth/token/", data={
                "client_key": key, "client_secret": secret, "grant_type": "refresh_token",
                "refresh_token": data["refresh_token"],
            }, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30)
            fresh = r.json()
            if "access_token" not in fresh:
                raise AuthError(f"TikTok token refresh failed — run `clipfactory auth tiktok` ({fresh})")
            _save(self.cfg, fresh)
            data = fresh
        return data["access_token"]

    def _post(self, path: str, token: str, body: dict) -> dict:
        r = session().post(f"{API}{path}", json=body, headers={
            "Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8",
        }, timeout=60)
        data = r.json()
        err = data.get("error") or {}
        if r.status_code >= 400 or err.get("code") not in (None, "ok"):
            msg = f"tiktok {path}: {err or r.text[:300]}"
            if r.status_code == 401 or err.get("code") in ("access_token_invalid", "scope_not_authorized", "scope_permission_missed"):
                raise AuthError(msg)
            raise PublishError(msg)
        return data.get("data") or {}

    def publish(self, clip: dict, copy: dict, slot_key: str) -> PostResult:
        token = self._token()
        path = Path(clip["path"])
        size = path.stat().st_size
        if size <= 64 * 1024 * 1024:
            chunk_size, chunks = size, 1
        else:
            chunk_size, chunks = CHUNK, math.floor(size / CHUNK)
        source_info = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk_size, "total_chunk_count": chunks}

        mode = self.settings.get("mode", "direct")
        if mode == "inbox":
            init = self._post("/post/publish/inbox/video/init/", token, {"source_info": source_info})
        else:
            info = self._post("/post/publish/creator_info/query/", token, {})
            wanted = self.settings.get("privacy", "PUBLIC_TO_EVERYONE")
            if not self.settings.get("audited", False):
                wanted = "SELF_ONLY"  # TikTok rejects anything else from apps that haven't passed the audit
            options = info.get("privacy_level_options") or [wanted]
            privacy = wanted if wanted in options else options[0]
            init = self._post("/post/publish/video/init/", token, {
                "post_info": {
                    "title": copy["tiktok_caption"],
                    "privacy_level": privacy,
                    "disable_duet": False,
                    "disable_comment": False,
                    "disable_stitch": False,
                    "video_cover_timestamp_ms": int((clip.get("duration") or 10) * 400),
                },
                "source_info": source_info,
            })

        publish_id, upload_url = init["publish_id"], init["upload_url"]
        with path.open("rb") as fh:
            for i in range(chunks):
                start = i * chunk_size
                length = size - start if i == chunks - 1 else chunk_size
                fh.seek(start)
                r = session().put(upload_url, data=fh.read(length), headers={
                    "Content-Type": "video/mp4", "Content-Length": str(length),
                    "Content-Range": f"bytes {start}-{start + length - 1}/{size}",
                }, timeout=600)
                if r.status_code not in (200, 201, 206):
                    raise PublishError(f"tiktok upload chunk {i} -> HTTP {r.status_code}")

        return self._wait(token, publish_id, timeout=900)

    def _wait(self, token: str, publish_id: str, timeout: float) -> PostResult:
        """Poll until TikTok finishes. Upload is already done, so any hiccup here means 'check later', never re-upload."""
        deadline = time.time() + timeout
        while True:
            try:
                status = self._post("/post/publish/status/fetch/", token, {"publish_id": publish_id})
            except AuthError:
                raise
            except Exception as e:  # noqa: BLE001
                raise PublishPending(publish_id, f"status check failed: {e}") from e
            state = status.get("status")
            if state in ("PUBLISH_COMPLETE", "SEND_TO_USER_INBOX"):
                ids = status.get("publicaly_available_post_id") or []
                return PostResult(remote_id=str(ids[0]) if ids else publish_id, url=None)
            if state == "FAILED":
                raise PublishError(f"tiktok publish failed: {status.get('fail_reason')}")
            if time.time() > deadline:
                raise PublishPending(publish_id)
            time.sleep(10)

    def check(self, remote_id: str) -> PostResult:
        return self._wait(self._token(), remote_id, timeout=0)
