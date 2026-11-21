"""YouTube publishing — the original /upload_youtube logic, made reusable.

Uses the same OAuth token file (youtube_token.json) and API calls as before.
"""
import logging
import os

from ..config import Config

log = logging.getLogger(__name__)
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def is_configured():
    return os.path.exists(Config.YOUTUBE_TOKEN_FILE)


def upload(video_path, title, description, tags=None, category_id="25", privacy="public",
           shorts=True, progress_fn=lambda p: None):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    if not is_configured():
        raise RuntimeError("YouTube is not connected: OAuth token file not found "
                           f"({os.path.basename(Config.YOUTUBE_TOKEN_FILE)}).")
    creds = Credentials.from_authorized_user_file(Config.YOUTUBE_TOKEN_FILE, SCOPES)
    youtube = build("youtube", "v3", credentials=creds)
    if shorts:
        title = title if "#shorts" in title.lower() else f"{title} #shorts"
        description = description if "#shorts" in description.lower() else f"{description}\n\n#shorts"
    body = {
        "snippet": {"title": title[:100], "description": description[:5000],
                    "tags": tags or ["news", "ai", "shorts"], "categoryId": str(category_id)},
        "status": {"privacyStatus": privacy},
    }
    media = MediaFileUpload(video_path, chunksize=8 * 1024 * 1024, resumable=True)
    req = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        status, response = req.next_chunk()
        if status:
            progress_fn(status.progress())
    video_id = response["id"]
    log.info("YouTube upload complete: %s", video_id)
    return {"videoId": video_id, "url": f"https://youtube.com/watch?v={video_id}"}
