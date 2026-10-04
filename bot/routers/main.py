import asyncio
import logging
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import NamedTuple

from aiogram import F, Router, html
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramServerError,
)
from aiogram.filters import CommandStart
from aiogram.types import Message, URLInputFile

from bot.misc import fetch_url_info
from bot.misc.schemas import FetchResponse, ItemInfo

router = Router(name="main")

MAX_CAPTION_LEN = 1024
DOWNLOAD_TIMEOUT = 120
NETWORK_ATTEMPTS = 2
RETRY_DELAY = 3.0

MediaSender = Callable[..., Awaitable[Message]]


class MediaStrategy(NamedTuple):
    """How one media type reaches Telegram.

    Only video takes a thumbnail, and only as an InputFile: SendVideo rejects a
    bare thumbnail URL, while SendPhoto silently drops the unknown field.
    """

    method: str
    supports_thumbnail: bool


# Strategy: one row per media type, resolved per message instance so subclasses
# and test doubles still dispatch. Adding a type = adding a row here.
STRATEGY_BY_TYPE: dict[str, MediaStrategy] = {
    "video": MediaStrategy("reply_video", supports_thumbnail=True),
    "image": MediaStrategy("reply_photo", supports_thumbnail=False),
}


def retry_without_thumbnail(fn: MediaSender) -> MediaSender:
    """Cross-cutting policy: Telegram rejects oversized/invalid thumbnails, retry once bare."""

    @wraps(fn)
    async def wrapper(*args: object, **kwargs: object) -> Message:
        for attempt in range(2):
            try:
                return await fn(*args, **kwargs)
            except TelegramBadRequest:
                if attempt or not kwargs.pop("thumbnail", None):
                    raise
                logging.warning("thumbnail rejected, retrying without it")
        raise RuntimeError("unreachable")

    return wrapper


def retry_on_network_error(fn: MediaSender) -> MediaSender:
    """Cross-cutting policy: transport hiccups and 5xx are worth one more attempt."""

    @wraps(fn)
    async def wrapper(*args: object, **kwargs: object) -> Message:
        for attempt in range(NETWORK_ATTEMPTS):
            try:
                return await fn(*args, **kwargs)
            except (TelegramNetworkError, TelegramServerError) as err:
                if attempt == NETWORK_ATTEMPTS - 1:
                    raise
                logging.warning(
                    "telegram network error (%s), retrying in %ss", err, RETRY_DELAY
                )
                await asyncio.sleep(RETRY_DELAY)
        raise RuntimeError("unreachable")

    return wrapper


@retry_without_thumbnail
async def dispatch(
    sender: MediaSender, file: URLInputFile, **kwargs: object
) -> Message:
    return await sender(file, **kwargs)


@retry_on_network_error
async def upload(message: Message, item: ItemInfo, caption: str = "") -> Message:
    strategy = STRATEGY_BY_TYPE[item.type]
    sender: MediaSender = getattr(message, strategy.method)
    media_url = item.download_url.encoded_string()
    kwargs: dict[str, object] = {"caption": caption[:MAX_CAPTION_LEN]}

    if strategy.supports_thumbnail and item.thumbnail_url:
        kwargs["thumbnail"] = URLInputFile(
            item.thumbnail_url.encoded_string(), timeout=DOWNLOAD_TIMEOUT
        )

    # Preferred path: Telegram's servers fetch the media themselves. Nothing is
    # uploaded, so a large file cannot outrun the session timeout.
    try:
        return await sender(media_url, **kwargs)
    except TelegramBadRequest as err:
        logging.info("telegram would not fetch the url (%s), streaming via bot", err)

    # Fallback: aiogram downloads and uploads. Slower, but survives CDNs that
    # reject Telegram's fetcher or that exceed the remote-size limits.
    return await dispatch(
        sender, URLInputFile(media_url, timeout=DOWNLOAD_TIMEOUT), **kwargs
    )


def resolve_items(info: FetchResponse) -> list[ItemInfo]:
    """Album payloads carry `items`; single-media payloads carry the fields inline."""
    if info.items:
        return info.items
    if info.download_url:
        return [ItemInfo.model_validate(info, from_attributes=True)]
    return []


async def send_item(message: Message, item: ItemInfo, caption: str = "") -> None:
    try:
        await upload(message, item, caption)
    except TelegramAPIError as err:
        logging.warning("send failed for %s: %s", item.type, err)
        await message.reply(html.bold("Yuklab bo'lmadi."))


@router.message(CommandStart())
async def hello(message: Message) -> None:
    await message.reply(
        f"{html.bold(f'Assalomu Alekum! {message.from_user.full_name[:10]}')}\n\nBotga hush kelibsiz!"
    )


@router.message(F.text)
async def process_url(message: Message) -> None:
    if not message.text:
        return

    info = await fetch_url_info(message.text)

    if info is None or not info.ok:
        await message.reply(html.bold("Xatolik: linkni qayta yuboring."))
        return

    for index, item in enumerate(resolve_items(info)):
        await send_item(message, item, info.caption if index == 0 else "")
