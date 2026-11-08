"""Posting to Instagram Reels, Facebook Page Reels and YouTube.

All three use direct (resumable) uploads of the video file, so the server does
not need a public HTTPS URL for the video.
"""
import json
import os
import time

import requests

TIMEOUT = 60


class PublishError(Exception):
    pass


def _graph(settings):
    ver = settings.get("graph_version") or "v23.0"
    return f"https://graph.facebook.com/{ver}"


def _check(resp, what):
    try:
        data = resp.json()
    except ValueError:
        data = {"raw": resp.text[:500]}
    if resp.status_code >= 400 or (isinstance(data, dict) and "error" in data):
        err = data.get("error", data) if isinstance(data, dict) else data
        msg = err.get("message") if isinstance(err, dict) else str(err)
        raise PublishError(f"{what}: {msg or resp.status_code}")
    return data


def _rupload(url, token, path):
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        resp = requests.post(
            url,
            headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size)},
            data=f,
            timeout=600,
        )
    return _check(resp, "video upload")


# ---------------------------------------------------------------- Meta helpers
def meta_check(settings):
    """Return a short description of the connected Page and Instagram account."""
    token, page_id = settings.get("meta_page_token"), settings.get("meta_page_id")
    if not token or not page_id:
        raise PublishError("Page ID ane Page token settings ma nathi")
    data = _check(
        requests.get(
            f"{_graph(settings)}/{page_id}",
            params={"fields": "name,instagram_business_account{username}", "access_token": token},
            timeout=TIMEOUT,
        ),
        "Facebook Page",
    )
    ig = data.get("instagram_business_account") or {}
    return {"page_name": data.get("name"), "ig_id": ig.get("id"), "ig_username": ig.get("username")}


def publish_instagram(settings, video_path, caption):
    token = settings.get("meta_page_token")
    ig_id = settings.get("ig_user_id") or meta_check(settings).get("ig_id")
    if not ig_id:
        raise PublishError("Instagram account Page sathe link nathi")
    g = _graph(settings)
    c = _check(
        requests.post(
            f"{g}/{ig_id}/media",
            data={"media_type": "REELS", "upload_type": "resumable", "caption": caption,
                  "share_to_feed": "true", "access_token": token},
            timeout=TIMEOUT,
        ),
        "Instagram container",
    )
    container, upload_uri = c["id"], c["uri"]
    _rupload(upload_uri, token, video_path)
    for _ in range(60):  # up to ~10 minutes of processing
        st = _check(
            requests.get(f"{g}/{container}", params={"fields": "status_code,status", "access_token": token},
                         timeout=TIMEOUT),
            "Instagram status",
        )
        code = st.get("status_code")
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            raise PublishError(f"Instagram processing: {st.get('status') or code}")
        time.sleep(10)
    else:
        raise PublishError("Instagram processing timeout")
    pub = _check(
        requests.post(f"{g}/{ig_id}/media_publish", data={"creation_id": container, "access_token": token},
                      timeout=TIMEOUT),
        "Instagram publish",
    )
    media_id = pub["id"]
    link = None
    try:
        link = requests.get(f"{g}/{media_id}", params={"fields": "permalink", "access_token": token},
                            timeout=TIMEOUT).json().get("permalink")
    except Exception:
        pass
    return {"id": media_id, "url": link}


def publish_facebook(settings, video_path, caption):
    token, page_id = settings.get("meta_page_token"), settings.get("meta_page_id")
    if not token or not page_id:
        raise PublishError("Page ID ane Page token settings ma nathi")
    g = _graph(settings)
    start = _check(
        requests.post(f"{g}/{page_id}/video_reels", data={"upload_phase": "start", "access_token": token},
                      timeout=TIMEOUT),
        "Facebook reel start",
    )
    video_id = start["video_id"]
    _rupload(start["upload_url"], token, video_path)
    _check(
        requests.post(
            f"{g}/{page_id}/video_reels",
            data={"upload_phase": "finish", "video_id": video_id, "video_state": "PUBLISHED",
                  "description": caption, "access_token": token},
            timeout=TIMEOUT,
        ),
        "Facebook reel publish",
    )
    return {"id": video_id, "url": f"https://www.facebook.com/reel/{video_id}"}


# ---------------------------------------------------------------- YouTube
YT_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
YT_REDIRECT = "http://localhost"


def youtube_auth_url(settings):
    from urllib.parse import urlencode
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": settings.get("yt_client_id", ""),
        "redirect_uri": YT_REDIRECT,
        "response_type": "code",
        "scope": YT_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
    })


def youtube_exchange_code(settings, code):
    from urllib.parse import parse_qs, urlparse
    code = code.strip()
    if code.startswith("http"):  # user pasted the whole localhost URL
        code = parse_qs(urlparse(code).query).get("code", [""])[0]
    data = _check(
        requests.post("https://oauth2.googleapis.com/token", data={
            "code": code, "client_id": settings.get("yt_client_id"),
            "client_secret": settings.get("yt_client_secret"),
            "redirect_uri": YT_REDIRECT, "grant_type": "authorization_code",
        }, timeout=TIMEOUT),
        "Google token",
    )
    if not data.get("refresh_token"):
        raise PublishError("Google e refresh token na aapyo. Pharithi 'Connect YouTube' karo.")
    return data["refresh_token"]


def _yt_access_token(settings):
    data = _check(
        requests.post("https://oauth2.googleapis.com/token", data={
            "client_id": settings.get("yt_client_id"), "client_secret": settings.get("yt_client_secret"),
            "refresh_token": settings.get("yt_refresh_token"), "grant_type": "refresh_token",
        }, timeout=TIMEOUT),
        "Google token refresh",
    )
    return data["access_token"]


def publish_youtube(settings, video_path, title, description, tags, privacy):
    if not settings.get("yt_refresh_token"):
        raise PublishError("YouTube connect nathi (Settings → Connect YouTube)")
    access = _yt_access_token(settings)
    size = os.path.getsize(video_path)
    body = {
        "snippet": {"title": title[:100], "description": description[:4900], "tags": tags[:30],
                    "categoryId": "25", "defaultLanguage": "hi", "defaultAudioLanguage": "hi"},
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False, "containsSyntheticMedia": True},
    }
    init = requests.post(
        "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
        headers={"Authorization": f"Bearer {access}", "Content-Type": "application/json; charset=UTF-8",
                 "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)},
        data=json.dumps(body),
        timeout=TIMEOUT,
    )
    if init.status_code >= 400:
        _check(init, "YouTube upload start")
    location = init.headers.get("Location")
    if not location:
        raise PublishError("YouTube upload URL na malyu")
    with open(video_path, "rb") as f:
        up = requests.put(location, headers={"Content-Type": "video/mp4", "Content-Length": str(size)},
                          data=f, timeout=1200)
    data = _check(up, "YouTube upload")
    vid = data["id"]
    return {"id": vid, "url": f"https://youtube.com/shorts/{vid}"}
