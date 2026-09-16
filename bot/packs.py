"""Создание эмодзи-пака в Telegram и получение id готовых эмодзи.

Весь диалог с Bot API по стикерам изолирован здесь. Если Telegram поменяет
лимиты или сигнатуры — правится только этот файл.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile, InputSticker

from .mosaic import Mosaic, build_mosaic
from .slicing.grid import GridPlan
from .slicing.static import Tile

#: Сколько элементов Bot API принимает за один createNewStickerSet.
CREATE_BATCH = 50

#: Максимальная длина title пака.
MAX_TITLE_LEN = 64

#: Максимальная длина короткого имени пака.
MAX_NAME_LEN = 64

#: Базовый эмодзи-триггер по умолчанию.
DEFAULT_TRIGGER = "\U0001f533"

_NAME_SAFE = re.compile(r"[^a-z0-9_]+")


class PackError(RuntimeError):
    """Пак создать не удалось, текст пригоден для показа пользователю."""


@dataclass(frozen=True)
class PackResult:
    name: str
    title: str
    link: str
    #: Соответствие «индекс тайла в сетке» → custom_emoji_id.
    emoji_ids: dict[int, str]

    def mosaic(self, plan: GridPlan) -> Mosaic:
        return build_mosaic(plan, self.emoji_ids)


def build_pack_name(bot_username: str, user_id: int) -> str:
    """Собирает короткое имя пака, удовлетворяющее правилам Telegram.

    Имя обязано оканчиваться на ``_by_<bot_username>`` и состоять из латиницы,
    цифр и подчёркиваний.
    """
    suffix = f"_by_{bot_username}"
    token = secrets.token_hex(3)
    stem = _NAME_SAFE.sub("", f"dibbuk{user_id}_{token}".lower()) or "dibbuk"
    room = MAX_NAME_LEN - len(suffix)
    if room < 1:
        raise PackError("слишком длинный username бота для имени пака")
    return f"{stem[:room]}{suffix}"


def build_pack_title(base: str, bot_username: str, *, branded: bool, suffix: str) -> str:
    """Готовит заголовок пака, при необходимости приклеивая ссылку на бота.

    Именно приписка в заголовке — основной канал роста: пак путешествует
    по чатам и приносит новых пользователей. Платный тариф её снимает.
    """
    title = base.strip() or "Emoji pack"
    if branded and suffix:
        tail = " " + suffix.format(bot=bot_username)
        room = MAX_TITLE_LEN - len(tail)
        if room > 0:
            title = f"{title[:room].rstrip()}{tail}"
    return title[:MAX_TITLE_LEN]


async def create_emoji_pack(
    bot: Bot,
    *,
    user_id: int,
    title: str,
    tiles: list[Tile],
    animated: bool,
    bot_username: str,
    attempts: int = 3,
) -> PackResult:
    """Создаёт пак из готовых тайлов и возвращает ссылку и id эмодзи."""
    if not tiles:
        raise PackError("нечего класть в пак — после нарезки не осталось ни одного тайла")

    sticker_format = "video" if animated else "static"
    extension = "webm" if animated else "png"
    stickers = [
        InputSticker(
            sticker=BufferedInputFile(tile.data, filename=f"e{tile.index}.{extension}"),
            format=sticker_format,
            emoji_list=[tile.emoji or DEFAULT_TRIGGER],
        )
        for tile in tiles
    ]

    last_error: Exception | None = None
    for _ in range(attempts):
        name = build_pack_name(bot_username, user_id)
        try:
            await bot.create_new_sticker_set(
                user_id=user_id,
                name=name,
                title=title,
                stickers=stickers[:CREATE_BATCH],
                sticker_type="custom_emoji",
            )
        except TelegramBadRequest as exc:
            last_error = exc
            if "occupied" in str(exc).lower():
                continue  # имя занято — берём новое и пробуем ещё раз
            raise PackError(_humanize(exc)) from exc

        try:
            for sticker in stickers[CREATE_BATCH:]:
                await bot.add_sticker_to_set(user_id=user_id, name=name, sticker=sticker)
            emoji_ids = await _collect_emoji_ids(bot, name, tiles)
        except TelegramBadRequest as exc:
            raise PackError(_humanize(exc)) from exc

        return PackResult(
            name=name,
            title=title,
            link=f"https://t.me/addemoji/{name}",
            emoji_ids=emoji_ids,
        )

    raise PackError(_humanize(last_error) if last_error else "не удалось создать пак")


async def _collect_emoji_ids(bot: Bot, name: str, tiles: list[Tile]) -> dict[int, str]:
    """Сопоставляет созданные эмодзи их позициям в исходной сетке.

    Telegram отдаёт элементы пака в том же порядке, в котором мы их загрузили,
    поэтому позиции восстанавливаются по индексам тайлов.
    """
    pack = await bot.get_sticker_set(name=name)
    ids: dict[int, str] = {}
    for tile, sticker in zip(tiles, pack.stickers):
        if sticker.custom_emoji_id:
            ids[tile.index] = sticker.custom_emoji_id
    return ids


def _humanize(exc: Exception) -> str:
    """Переводит типовые ошибки Bot API в понятный пользователю текст."""
    message = str(exc).lower()
    if "stickers_too_much" in message or "too much" in message:
        return "в паке слишком много эмодзи — уменьшите число колонок"
    if "sticker_video_long" in message or "duration" in message:
        return "анимация длиннее 3 секунд — обрежьте её и пришлите снова"
    if "file is too big" in message or "too big" in message:
        return "файл слишком тяжёлый, попробуйте картинку поменьше"
    if "invalid sticker emojis" in message:
        return "Telegram не принял эмодзи-триггер, попробуйте ещё раз"
    if "peer_id_invalid" in message or "never interacted" in message:
        return "напишите боту /start и повторите — Telegram требует этого для создания пака"
    return f"Telegram отклонил запрос: {exc}"
