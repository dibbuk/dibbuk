"""Точка входа: собирает диспетчер, проверяет окружение и запускает polling."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from .config import Settings, settings
from .handlers import router
from .slicing.animated import FFmpegMissingError, ensure_ffmpeg
from .storage import Storage, storage

log = logging.getLogger("dibbuk")


def build_dispatcher(app_settings: Settings, app_storage: Storage) -> Dispatcher:
    """Собирает диспетчер и прокидывает зависимости в обработчики."""
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    # Ключи попадают в обработчики как именованные аргументы.
    dispatcher["app_storage"] = app_storage
    dispatcher["settings"] = app_settings
    return dispatcher


def check_animation_support(app_settings: Settings) -> None:
    """Если ffmpeg нет, глушим анимацию заранее, а не в момент запроса пользователя."""
    if not app_settings.enable_animation:
        return
    try:
        ensure_ffmpeg()
    except FFmpegMissingError as exc:
        app_settings.enable_animation = False
        log.warning("%s — анимированные паки отключены", exc)


async def run() -> None:
    settings.validate()
    check_animation_support(settings)

    bot = Bot(
        token=settings.token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = build_dispatcher(settings, storage)

    me = await bot.me()
    log.info("запускаюсь как @%s", me.username)

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dispatcher.start_polling(bot)
    finally:
        await bot.session.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log.info("остановлен вручную")


if __name__ == "__main__":
    main()
