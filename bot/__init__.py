from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode

from bot.routers import register_routers
from config import settings

# aiogram's 60s default covers the whole POST. Media bytes travel through this
# session, so a full-size video upload outruns it and raises TelegramNetworkError.
REQUEST_TIMEOUT = 300.0


async def main() -> None:
    dp = Dispatcher()
    bot = Bot(
        settings.BOT_TOKEN.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        session=AiohttpSession(timeout=REQUEST_TIMEOUT),
    )

    register_routers(dp)

    await dp.start_polling(bot)
