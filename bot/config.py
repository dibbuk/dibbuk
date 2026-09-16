"""Настройки бота. Всё, что отличается между окружениями, живёт только здесь."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_str(name: str, default: str) -> str:
    """Читает строку, снимая обрамляющие кавычки.

    Кавычки нужны в .env, чтобы значения вроде ``| @{bot}`` пережили
    и systemd, и sourcing в shell. Docker Compose кавычки не снимает,
    поэтому делаем это сами и одинаково для всех способов запуска.
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    """Конфигурация из переменных окружения."""

    token: str = field(default_factory=lambda: _env_str("BOT_TOKEN", ""))

    #: Максимальный размер входного файла. Bot API всё равно не отдаст больше 20 МБ.
    max_input_bytes: int = field(
        default_factory=lambda: _env_int("MAX_INPUT_BYTES", 20 * 1024 * 1024)
    )

    #: Сколько нарезок в сутки доступно на бесплатном тарифе.
    free_daily_limit: int = field(
        default_factory=lambda: _env_int("FREE_DAILY_LIMIT", 5)
    )

    #: Приписка к названию пака на бесплатном тарифе — это и пейволл, и вирусная петля.
    #: Ровно та механика, которая единственная доказанно монетизируется в нише.
    branding_suffix: str = field(
        default_factory=lambda: _env_str("BRANDING_SUFFIX", "| @{bot}")
    )

    #: Цена снятия брендинга с одного пака, в Telegram Stars.
    stars_price_unbrand: int = field(
        default_factory=lambda: _env_int("STARS_PRICE_UNBRAND", 25)
    )

    #: Цена безлимитной подписки на месяц, в Telegram Stars.
    stars_price_subscription: int = field(
        default_factory=lambda: _env_int("STARS_PRICE_SUBSCRIPTION", 250)
    )

    #: Разрешать ли анимированные паки (требует ffmpeg с libvpx-vp9).
    enable_animation: bool = field(
        default_factory=lambda: _env_bool("ENABLE_ANIMATION", True)
    )

    #: Собирать ли картинку из эмодзи прямо в сообщении.
    #: Telegram разрешает боту слать custom_emoji, только если у владельца бота
    #: есть Premium либо боту куплен юзернейм на Fragment. Без этого пак всё равно
    #: создаётся, а мозаика молча отключается после первой же отказной попытки.
    enable_mosaic: bool = field(
        default_factory=lambda: _env_bool("ENABLE_MOSAIC", True)
    )

    def validate(self) -> None:
        if not self.token:
            raise RuntimeError(
                "BOT_TOKEN не задан. Скопируйте .env.example в .env и впишите токен "
                "от @BotFather."
            )


settings = Settings()
