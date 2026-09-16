"""Состояние пользователей и счётчики тарифа.

Реализация в памяти: её достаточно для одного процесса и для запуска MVP.
Интерфейс намеренно узкий, чтобы подменить его на Redis или Postgres,
не трогая обработчики.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Literal

from .slicing.grid import MAX_PADDING, MIN_PADDING, clamp_padding

FitMode = Literal["cover", "contain"]


@dataclass
class UserSettings:
    """Настройки нарезки, которые пользователь крутит кнопками."""

    cols: int | None = None  # None — автоподбор сетки
    padding: float = 0.0
    fit: FitMode = "cover"
    drop_background: bool = False

    def with_padding(self, delta: float) -> None:
        self.padding = round(clamp_padding(self.padding + delta), 3)

    @property
    def padding_label(self) -> str:
        if self.padding == 0:
            return "0"
        return f"{self.padding:+.2f}".rstrip("0").rstrip(".")

    @property
    def padding_at_limit(self) -> bool:
        return self.padding <= MIN_PADDING or self.padding >= MAX_PADDING


@dataclass
class PendingJob:
    """Скачанный, но ещё не нарезанный исходник.

    Файл лежит во временной директории, а не в памяти: видео до 20 МБ на
    каждого активного пользователя быстро съели бы процесс.
    """

    path: Path
    is_animated: bool
    width: int
    height: int
    #: id сообщения с панелью настроек — чтобы редактировать её на месте.
    panel_message_id: int | None = None

    def discard(self) -> None:
        """Убирает временный файл. Вызывается и при успехе, и при отмене."""
        self.path.unlink(missing_ok=True)


@dataclass
class UserState:
    settings: UserSettings = field(default_factory=UserSettings)
    job: PendingJob | None = None
    #: Сколько паков уже сделано сегодня и когда счётчик обнулялся.
    packs_today: int = 0
    counter_day: date = field(default_factory=date.today)
    #: Оплаченные разблокировки брендинга, которые ещё не потрачены.
    unbrand_credits: int = 0
    #: До какой даты действует безлимитная подписка.
    subscription_until: date | None = None

    def roll_over(self) -> None:
        """Сбрасывает суточный счётчик при смене даты."""
        today = date.today()
        if self.counter_day != today:
            self.counter_day = today
            self.packs_today = 0

    @property
    def is_subscribed(self) -> bool:
        return self.subscription_until is not None and self.subscription_until >= date.today()

    def quota_left(self, daily_limit: int) -> int:
        self.roll_over()
        if self.is_subscribed:
            return daily_limit  # подписка снимает лимит, значение только для текста
        return max(0, daily_limit - self.packs_today)

    def should_brand(self) -> bool:
        """Нужно ли клеить ссылку на бота в название пака."""
        if self.is_subscribed:
            return False
        return self.unbrand_credits <= 0

    def consume_unbrand(self) -> None:
        if not self.is_subscribed and self.unbrand_credits > 0:
            self.unbrand_credits -= 1


class Storage:
    """Простейшее хранилище состояний в памяти процесса."""

    def __init__(self) -> None:
        self._users: dict[int, UserState] = {}

    def get(self, user_id: int) -> UserState:
        state = self._users.get(user_id)
        if state is None:
            state = UserState()
            self._users[user_id] = state
        state.roll_over()
        return state

    def reset_job(self, user_id: int) -> None:
        state = self.get(user_id)
        if state.job is not None:
            state.job.discard()
        state.job = None

    @property
    def size(self) -> int:
        return len(self._users)


storage = Storage()
