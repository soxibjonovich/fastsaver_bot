from aiogram import Dispatcher

from . import main


def register_routers(dp: Dispatcher) -> None:
    for router in [main.router]:
        dp.include_router(router)
