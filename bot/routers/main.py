import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from functools import wraps

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
logger = logging.getLogger(__name__)


MAX_CAPTION_LEN = 1024
DOWNLOAD_TIMEOUT = 120
NETWORK_ATTEMPTS = 2
RETRY_DELAY = 3.0

Sender = Callable[..., Awaitable[Message]]


# --------------------------------------------------------------------------- #
# Strategies: ONLY know "which Message method" and "which kwargs for this type".
# They know nothing about retries or delivery.
# --------------------------------------------------------------------------- #
class MediaStrategy(ABC):
    @property
    @abstractmethod
    def method(self) -> str:
        """Name of the aiogram Message method used to send this media type."""

    def build_kwargs(self, item: ItemInfo, caption: str) -> dict[str, object]:
        kwargs: dict[str, object] = {"caption": caption[:MAX_CAPTION_LEN]}
        if item.width:
            kwargs["width"] = item.width
        if item.height:
            kwargs["height"] = item.height
        return kwargs


class VideoStrategy(MediaStrategy):
    """Only video takes a thumbnail, and only as an InputFile: SendVideo rejects a
    bare thumbnail URL, while SendPhoto silently drops the unknown field."""

    method = "reply_video"

    def build_kwargs(self, item: ItemInfo, caption: str) -> dict[str, object]:
        kwargs = super().build_kwargs(item, caption)
        if item.thumbnail_url:
            kwargs["thumbnail"] = URLInputFile(
                item.thumbnail_url.encoded_string(), timeout=DOWNLOAD_TIMEOUT
            )
        return kwargs


class PhotoStrategy(MediaStrategy):
    method = "reply_photo"


STRATEGIES: dict[str, MediaStrategy] = {
    "video": VideoStrategy(),
    "image": PhotoStrategy(),
}


# --------------------------------------------------------------------------- #
# Cross-cutting retry policies (decorators)
# --------------------------------------------------------------------------- #
def retry_without_thumbnail(fn: Sender) -> Sender:
    """Telegram rejects oversized/invalid thumbnails: retry once bare."""

    @wraps(fn)
    async def wrapper(*args: object, **kwargs: object) -> Message:
        for attempt in range(2):
            try:
                return await fn(*args, **kwargs)
            except TelegramBadRequest:
                if attempt or not kwargs.pop("thumbnail", None):
                    raise
                logger.warning("thumbnail rejected, retrying without it")
        raise RuntimeError("unreachable")

    return wrapper


def retry_on_network_error(fn: Sender) -> Sender:
    """Transport hiccups and 5xx are worth one more attempt."""

    @wraps(fn)
    async def wrapper(*args: object, **kwargs: object) -> Message:
        for attempt in range(NETWORK_ATTEMPTS):
            try:
                return await fn(*args, **kwargs)
            except (TelegramNetworkError, TelegramServerError) as err:
                if attempt == NETWORK_ATTEMPTS - 1:
                    raise
                logger.warning(
                    "telegram network error (%s), retrying in %ss", err, RETRY_DELAY
                )
                await asyncio.sleep(RETRY_DELAY)
        raise RuntimeError("unreachable")

    return wrapper


# --------------------------------------------------------------------------- #
# Context: picks a strategy and delivers. Strategies no longer call back into
# module-level helpers, so the dependency flows one way.
# --------------------------------------------------------------------------- #
@retry_without_thumbnail
async def _upload_via_bot(sender: Sender, file: URLInputFile, **kwargs: object) -> Message:
    return await sender(file, **kwargs)


@retry_on_network_error
async def _deliver(
    sender: Sender, media_url: str, kwargs: dict[str, object]
) -> Message:
    # Preferred: Telegram's servers fetch the media; nothing is uploaded by us.
    try:
        return await sender(media_url, **kwargs)
    except TelegramBadRequest as err:
        logger.info("telegram would not fetch the url (%s), streaming via bot", err)

    # Fallback: aiogram downloads and uploads. Slower, but survives CDNs that
    # reject Telegram's fetcher or exceed remote-size limits.
    return await _upload_via_bot(
        sender, URLInputFile(media_url, timeout=DOWNLOAD_TIMEOUT), **kwargs
    )


class MediaSender:
    """Context for the strategy pattern."""

    def __init__(self, strategies: dict[str, MediaStrategy]) -> None:
        self._strategies = strategies

    async def send(self, message: Message, item: ItemInfo, caption: str = "") -> Message:
        strategy = self._strategies[item.type]
        sender: Sender = getattr(message, strategy.method)
        return await _deliver(
            sender, item.download_url.encoded_string(), strategy.build_kwargs(item, caption)
        )


media_sender = MediaSender(STRATEGIES)


# --------------------------------------------------------------------------- #
# Handlers
# --------------------------------------------------------------------------- #
def resolve_items(info: FetchResponse) -> list[ItemInfo]:
    """Album payloads carry `items`; single-media payloads carry the fields inline."""
    if info.items:
        return info.items
    if info.download_url:
        return [ItemInfo.model_validate(info, from_attributes=True)]
    return []


async def send_item(message: Message, item: ItemInfo, caption: str = "") -> None:
    try:
        await media_sender.send(message, item, caption)
    except (TelegramAPIError, KeyError) as err:  # KeyError: unknown media type
        logger.warning("send failed for %s: %s", item.type, err)
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