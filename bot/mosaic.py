"""Сборка картинки из кастомных эмодзи прямо в тексте сообщения.

Это главный демо-приём ниши: пользователь получает не только ссылку на пак,
но и саму картинку, собранную внутри сообщения. Работает потому, что боты
отправляют кастомные эмодзи без Telegram Premium — в отличие от людей.

Модуль чистый: никакого aiogram, только текст и смещения. Смещения считаются
в кодовых единицах UTF-16, как того требует Bot API.
"""

from __future__ import annotations

from dataclasses import dataclass

from .slicing.grid import GridPlan

#: Символ-носитель, поверх которого Telegram рисует кастомный эмодзи.
#: Лежит в BMP, поэтому занимает ровно одну кодовую единицу UTF-16.
PLACEHOLDER = "⬜"

#: Чем заполняем позицию, для которой тайла нет (например, его вырезал фон).
GAP = " "  # EM SPACE — держит ширину и не схлопывается


@dataclass(frozen=True)
class MosaicEntity:
    """Одна сущность custom_emoji для Bot API."""

    offset: int
    length: int
    custom_emoji_id: str


@dataclass(frozen=True)
class Mosaic:
    text: str
    entities: list[MosaicEntity]

    @property
    def is_empty(self) -> bool:
        return not self.entities


def utf16_length(text: str) -> int:
    """Длина строки в кодовых единицах UTF-16 — единица измерения Bot API."""
    return len(text.encode("utf-16-le")) // 2


def build_mosaic(plan: GridPlan, emoji_ids: dict[int, str]) -> Mosaic:
    """Собирает текст-мозаику и сущности к нему.

    ``emoji_ids`` — соответствие «индекс тайла в сетке» → custom_emoji_id.
    Пропуски допустимы: на их месте окажется пробел, и картинка не съедет.
    """
    parts: list[str] = []
    entities: list[MosaicEntity] = []
    offset = 0

    for row in range(plan.rows):
        if row:
            parts.append("\n")
            offset += 1
        for col in range(plan.cols):
            index = row * plan.cols + col
            emoji_id = emoji_ids.get(index)
            if emoji_id is None:
                parts.append(GAP)
                offset += utf16_length(GAP)
                continue
            parts.append(PLACEHOLDER)
            length = utf16_length(PLACEHOLDER)
            entities.append(
                MosaicEntity(offset=offset, length=length, custom_emoji_id=emoji_id)
            )
            offset += length

    return Mosaic(text="".join(parts), entities=entities)


def mosaic_fits_message(mosaic: Mosaic, limit: int = 4096) -> bool:
    """Влезает ли мозаика в лимит длины сообщения Telegram."""
    return utf16_length(mosaic.text) <= limit
