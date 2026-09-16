"""Инлайн-клавиатуры панели настроек."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .slicing.grid import MAX_COLS, MIN_COLS
from .storage import UserSettings

#: Шаг изменения полей одной кнопкой.
PADDING_STEP = 0.02


def settings_panel(config: UserSettings, cols: int) -> InlineKeyboardMarkup:
    """Панель под превью: сетка, поля, кадрирование, фон и запуск нарезки."""
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(text="◀ колонки", callback_data="cols:-1"),
        InlineKeyboardButton(text=f"{cols}", callback_data="cols:auto"),
        InlineKeyboardButton(text="колонки ▶", callback_data="cols:+1"),
    )
    builder.row(
        InlineKeyboardButton(text="◀ поля", callback_data="pad:-1"),
        InlineKeyboardButton(text=config.padding_label, callback_data="pad:0"),
        InlineKeyboardButton(text="поля ▶", callback_data="pad:+1"),
    )
    builder.row(
        InlineKeyboardButton(
            text=f"Кадр: {fit_label(config.fit)}", callback_data="fit:toggle"
        ),
        InlineKeyboardButton(
            text=f"Фон: {background_label(config.drop_background)}",
            callback_data="bg:toggle",
        ),
    )
    builder.row(InlineKeyboardButton(text="Нарезать", callback_data="run"))
    return builder.as_markup()


def fit_label(fit: str) -> str:
    return "заполнить" if fit == "cover" else "вписать"


def background_label(drop: bool) -> str:
    return "убрать" if drop else "оставить"


def shift_cols(current: int, delta: int) -> int:
    """Двигает число колонок в допустимых границах."""
    return max(MIN_COLS, min(MAX_COLS, current + delta))
