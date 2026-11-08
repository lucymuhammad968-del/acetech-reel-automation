"""Post the next due item from queue.csv to Instagram via the Graph API.

Supports Reels (.mp4/.mov) and single image posts (.jpg/.jpeg).
Files live in the media/ folder of the repo.

Env vars (GitHub Actions secrets):
  IG_USER_ID       Instagram Business/Creator account ID
  IG_ACCESS_TOKEN  Access token with instagram_content_publish
  IG_GRAPH_BASE    (optional) override; default https://graph.facebook.com/v21.0
"""
import csv
import os
import sys
import time
from datetime import datetime, timezone
from urllib.parse import quote

import requests

GRAPH = os.environ.get("IG_GRAPH_BASE") or "https://graph.facebook.com/v21.0"
QUEUE = "queue.csv"
MEDIA_DIR = "media"
FIELDS = ["filename", "caption", "post_at", "status", "media_id"]
IMAGE_EXT = (".jpg", ".jpeg")
VIDEO_EXT = (".mp4", ".mov")


def load_queue():
    with open(QUEUE, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for k in FIELDS:
            r.setdefault(k, "")
    return rows


def save_queue(rows):
    with open(QUEUE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def parse_time(s):
    dt = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def api(method, path, **params):
    params["access_token"] = os.environ["IG_ACCESS_TOKEN"]
    r = requests.request(method, f"{GRAPH}/{path}",
                         data=params if method == "POST" else None,
                         params=params if method == "GET" else None, timeout=60)
    data = r.json()
    if r.status_code >= 400 or "error" in data:
        raise RuntimeError(f"Graph API error: {data}")
    return data


def publish(media_url, caption, is_video):
    uid = os.environ["IG_USER_ID"]
    if is_video:
        container = api("POST", f"{uid}/media", media_type="REELS",
                        video_url=media_url, caption=caption)["id"]
    else:
        container = api("POST", f"{uid}/media", image_url=media_url,
                        caption=caption)["id"]
    print(f"Container created: {container}")
    for _ in range(60):  # up to ~10 minutes
        status = api("GET", container, fields="status_code,status")
        code = status.get("status_code")
        print(f"Processing status: {code}")
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Media processing failed: {status}")
        time.sleep(10)
    else:
        raise RuntimeError("Timed out waiting for media processing")
    return api("POST", f"{uid}/media_publish", creation_id=container)["id"]


def main():
    rows = load_queue()
    now = datetime.now(timezone.utc)
    due = next((r for r in rows if not r["status"].strip()
                and parse_time(r["post_at"]) <= now), None)
    if not due:
        print("Nothing due.")
        return 0

    repo = os.environ["GITHUB_REPOSITORY"]
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    name = os.path.basename(due["filename"])
    ext = os.path.splitext(name)[1].lower()
    if ext not in IMAGE_EXT + VIDEO_EXT:
        due["status"] = f"failed: unsupported file type {ext}"
        save_queue(rows)
        print(due["status"], file=sys.stderr)
        return 1
    is_video = ext in VIDEO_EXT
    url = f"https://raw.githubusercontent.com/{repo}/{branch}/{MEDIA_DIR}/{quote(name)}"
    print(f"Posting {name} ({'reel' if is_video else 'image'}) from {url}")

    due["status"] = "posting"      # mark first: never double-post
    save_queue(rows)
    try:
        due["media_id"] = publish(url, due["caption"], is_video)
        due["status"] = "posted"
        try:
            os.remove(os.path.join(MEDIA_DIR, name))   # keep repo small
            print("Deleted file from repo.")
        except OSError as e:
            print(f"Could not delete file: {e}", file=sys.stderr)
    except Exception as e:
        due["status"] = f"failed: {e}"[:200].replace("\n", " ")
        save_queue(rows)
        print(due["status"], file=sys.stderr)
        return 1
    save_queue(rows)
    print(f"Published! media id {due['media_id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
