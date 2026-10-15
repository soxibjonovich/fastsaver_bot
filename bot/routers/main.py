import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from functools import wraps
from typing import Any

from aiogram import F, Router, html
from aiogram.enums import ChatAction
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramServerError,
)
from aiogram.filters import CommandStart
from aiogram.types import (
    InputFile,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
    URLInputFile,
)

from bot.misc import fetch_url_info
from bot.misc.schemas import FetchResponse, ItemInfo

router = Router(name="main")
logger = logging.getLogger(__name__)


MAX_CAPTION_LEN = 1024
DOWNLOAD_TIMEOUT = 120
NETWORK_ATTEMPTS = 2
RETRY_DELAY = 3.0
ALBUM_LIMIT = 10  # Telegram accepts 2-10 media per sendMediaGroup call
CHAT_ACTION_TTL = 4.0  # Telegram clears a chat action after ~5s of silence

Sender = Callable[..., Awaitable[Message]]
AlbumSender = Callable[..., Awaitable[list[Message]]]


# --------------------------------------------------------------------------- #
# Strategies: ONLY know "which Message method" and "which kwargs for this type".
# They know nothing about retries or delivery.
# --------------------------------------------------------------------------- #
def _media_source(item: ItemInfo, upload: bool) -> str | InputFile:
    """A remote URL lets Telegram fetch the file itself; an InputFile makes
    aiogram download and stream it instead."""
    url = item.download_url.encoded_string()
    if upload:
        return URLInputFile(url, timeout=DOWNLOAD_TIMEOUT)
    return url


class MediaStrategy(ABC):
    @property
    @abstractmethod
    def method(self) -> str:
        """Name of the aiogram Message method used to send this media type."""

    @abstractmethod
    def build_input_media(
        self,
        item: ItemInfo,
        caption: str = "",
        *,
        upload: bool = False,
        with_thumbnail: bool = True,
    ) -> InputMediaPhoto | InputMediaVideo:
        """The item as one entry of a sendMediaGroup album."""

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

    def build_input_media(
        self,
        item: ItemInfo,
        caption: str = "",
        *,
        upload: bool = False,
        with_thumbnail: bool = True,
    ) -> InputMediaVideo:
        kwargs: dict[str, object] = {"media": _media_source(item, upload)}
        if caption:
            kwargs["caption"] = caption[:MAX_CAPTION_LEN]
        if item.width:
            kwargs["width"] = item.width
        if item.height:
            kwargs["height"] = item.height
        if item.duration:
            kwargs["duration"] = item.duration
        if with_thumbnail and item.thumbnail_url:
            kwargs["thumbnail"] = URLInputFile(
                item.thumbnail_url.encoded_string(), timeout=DOWNLOAD_TIMEOUT
            )
        return InputMediaVideo(**kwargs)


class PhotoStrategy(MediaStrategy):
    method = "reply_photo"

    def build_input_media(
        self,
        item: ItemInfo,
        caption: str = "",
        *,
        upload: bool = False,
        with_thumbnail: bool = True,
    ) -> InputMediaPhoto:
        # InputMediaPhoto takes no width/height; Telegram derives them.
        return InputMediaPhoto(
            media=_media_source(item, upload),
            caption=caption[:MAX_CAPTION_LEN] or None,
        )


STRATEGIES: dict[str, MediaStrategy] = {
    "video": VideoStrategy(),
    "image": PhotoStrategy(),
}


# --------------------------------------------------------------------------- #
# Cross-cutting retry policies (decorators)
# --------------------------------------------------------------------------- #
def retry_without_thumbnail[T](
    fn: Callable[..., Awaitable[T]],
) -> Callable[..., Awaitable[T]]:
    """Telegram rejects oversized/invalid thumbnails: retry once bare."""

    @wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> T:
        for attempt in range(2):
            try:
                return await fn(*args, **kwargs)
            except TelegramBadRequest:
                if attempt or not kwargs.pop("thumbnail", None):
                    raise
                logger.warning("thumbnail rejected, retrying without it")
        raise RuntimeError("unreachable")

    return wrapper


def retry_on_network_error[T](
    fn: Callable[..., Awaitable[T]],
) -> Callable[..., Awaitable[T]]:
    """Transport hiccups and 5xx are worth one more attempt."""

    @wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> T:
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
async def _upload_via_bot(
    sender: Sender, file: URLInputFile, **kwargs: object
) -> Message:
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


# --------------------------------------------------------------------------- #
# Album: several items ride in ONE sendMediaGroup instead of one message each.
# --------------------------------------------------------------------------- #
def build_album_media(
    items: list[ItemInfo],
    caption: str,
    *,
    upload: bool = False,
    with_thumbnail: bool = True,
) -> list[InputMediaPhoto | InputMediaVideo]:
    """Caption rides on the first item only: Telegram shows one per album."""
    media: list[InputMediaPhoto | InputMediaVideo] = []
    for index, item in enumerate(items):
        strategy = STRATEGIES[item.type]
        media.append(
            strategy.build_input_media(
                item,
                caption if index == 0 else "",
                upload=upload,
                with_thumbnail=with_thumbnail,
            )
        )
    return media


async def _upload_album(
    sender: AlbumSender, items: list[ItemInfo], caption: str
) -> list[Message]:
    """Telegram rejects oversized/invalid thumbnails: retry the group without them."""
    for with_thumbnail in (True, False):
        try:
            return await sender(
                build_album_media(
                    items, caption, upload=True, with_thumbnail=with_thumbnail
                )
            )
        except TelegramBadRequest:
            if not with_thumbnail or not any(item.thumbnail_url for item in items):
                raise
            logger.warning("album thumbnail rejected, retrying without them")
    raise RuntimeError("unreachable")


@retry_on_network_error
async def _deliver_album(
    sender: AlbumSender, items: list[ItemInfo], caption: str
) -> list[Message]:
    # Preferred: Telegram's servers fetch the media; nothing is uploaded by us.
    try:
        return await sender(build_album_media(items, caption))
    except TelegramBadRequest as err:
        logger.info("telegram would not fetch the url (%s), streaming via bot", err)

    # Fallback: aiogram downloads and uploads. Slower, but survives CDNs that
    # reject Telegram's fetcher or exceed remote-size limits.
    return await _upload_album(sender, items, caption)


# --------------------------------------------------------------------------- #
# Chat actions: the "typing…" / "sending photo…" hint. Telegram clears an action
# after ~5s, so a background task re-arms it while we fetch and upload.
# --------------------------------------------------------------------------- #
UPLOAD_ACTION: dict[str, ChatAction] = {
    "video": ChatAction.UPLOAD_VIDEO,
    "image": ChatAction.UPLOAD_PHOTO,
}


async def _repeat_chat_action(message: Message, action: ChatAction) -> None:
    while True:
        try:
            await message.bot.send_chat_action(
                message.chat.id, action, message_thread_id=message.message_thread_id
            )
        except TelegramAPIError as err:  # a hint is never worth failing the send
            logger.debug("chat action failed: %s", err)
            return
        await asyncio.sleep(CHAT_ACTION_TTL)


@asynccontextmanager
async def chat_action(message: Message, action: ChatAction) -> AsyncIterator[None]:
    task = asyncio.create_task(_repeat_chat_action(message, action))
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


class MediaSender:
    """Context for the strategy pattern."""

    def __init__(self, strategies: dict[str, MediaStrategy]) -> None:
        self._strategies = strategies

    async def send(
        self, message: Message, item: ItemInfo, caption: str = ""
    ) -> Message:
        strategy = self._strategies[item.type]
        sender: Sender = getattr(message, strategy.method)
        return await _deliver(
            sender,
            item.download_url.encoded_string(),
            strategy.build_kwargs(item, caption),
        )

    async def send_album(
        self, message: Message, items: list[ItemInfo], caption: str = ""
    ) -> None:
        """One album per up-to-ten items; a lone remainder goes on its own."""
        for start in range(0, len(items), ALBUM_LIMIT):
            chunk = items[start : start + ALBUM_LIMIT]
            chunk_caption = caption if start == 0 else ""
            if len(chunk) == 1:
                await self.send(message, chunk[0], chunk_caption)
            else:
                await _deliver_album(message.reply_media_group, chunk, chunk_caption)


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
    action = UPLOAD_ACTION.get(item.type, ChatAction.UPLOAD_DOCUMENT)
    try:
        async with chat_action(message, action):
            await media_sender.send(message, item, caption)
    except (TelegramAPIError, KeyError) as err:  # KeyError: unknown media type
        logger.warning("send failed for %s: %s", item.type, err)
        await message.reply(html.bold("Yuklab bo'lmadi."))


async def send_album(
    message: Message, items: list[ItemInfo], caption: str = ""
) -> None:
    # Telegram only knows one hint per request: video when the album is all video.
    action = (
        ChatAction.UPLOAD_VIDEO
        if all(item.type == "video" for item in items)
        else ChatAction.UPLOAD_PHOTO
    )
    try:
        async with chat_action(message, action):
            await media_sender.send_album(message, items, caption)
    except (TelegramAPIError, KeyError) as err:  # KeyError: unknown media type
        logger.warning("album send failed: %s", err)
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

    async with chat_action(message, ChatAction.TYPING):
        info = await fetch_url_info(message.text)

    if info is None or not info.ok:
        await message.reply(html.bold("Xatolik: linkni qayta yuboring."))
        return

    items = resolve_items(info)
    if len(items) < 2:
        for item in items:
            await send_item(message, item, info.caption or "")
        return

    await send_album(message, items, info.caption or "")
