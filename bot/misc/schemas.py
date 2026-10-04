from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl

Source = Literal["instagram.com", "tiktok.com"]
MediaType = Literal["video", "image", "album"]


# Instagram — album carries no top-level download_url, media lives in items[]:
# {
#   "ok": true,
#   "id": "CUbHfhpswxt",
#   "source": "instagram.com",
#   "type": "album",
#   "duration": null,
#   "caption": "…",
#   "thumbnail_url": "https://…",
#   "items": [{"type": "image", "download_url": "https://…", "width": 1080,
#              "height": 1350, "thumbnail_url": "https://…"}]
# }
#
# TikTok video — always single media, plus audio metadata:
# {
#   "ok": true,
#   "id": "6718335390845095173",
#   "source": "tiktok.com",
#   "type": "video",
#   "download_url": "https://v16e.tiktokcdn.com/…",
#   "width": 720, "height": 1280,
#   "thumbnail_url": "https://p16-common-sign.tiktokcdn.com/…",
#   "duration": 10,
#   "caption": "…",
#   "music_url": "https://sf9-ies-music-va.tiktokcdn.com/…",
#   "music": {"id": null, "title": null, "author": null, "cover": null,
#             "duration": 10, "album": null, "original": null,
#             "download_url": "https://sf9-ies-music-va.tiktokcdn.com/…"}
# }
#
# TikTok photo mode — type "image", items[] empty, audio still attached.
#
# Failures answer HTTP 400 with {"ok": false, "detail": "fetch.failed"}.
class MusicInfo(BaseModel):
    id: str | None = None
    title: str | None = None
    author: str | None = None
    cover: str | None = None
    duration: int | None = None
    album: Any | None = None
    original: Any | None = None
    download_url: HttpUrl | None = None


class ItemInfo(BaseModel):
    type: Literal["video", "image"]
    download_url: HttpUrl
    width: int | None = None
    height: int | None = None
    thumbnail_url: HttpUrl | None = None
    duration: int | None = None
    caption: str | None = None


class FetchResponse(BaseModel):
    ok: bool
    id: str
    source: Source
    type: MediaType
    items: list[ItemInfo] = Field(default_factory=list)
    download_url: HttpUrl | None = None
    width: int | None = None
    height: int | None = None
    thumbnail_url: HttpUrl | None = None
    duration: int | None = None
    caption: str | None = None
    music: MusicInfo | None = None
    music_url: HttpUrl | None = None
