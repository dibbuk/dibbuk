"""Геометрия нарезки: сколько тайлов, какого размера и как они лягут в сообщение.

Модуль намеренно чистый — без Pillow, ffmpeg и Telegram. Вся математика сетки
тестируется изолированно, потому что именно здесь живут краевые случаи,
на которых конкуренты ломаются: слишком узкие картинки, переполнение пака,
вырожденные соотношения сторон.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil

#: Сторона одного кастомного эмодзи в пикселях. Требование Telegram, не настройка.
TILE_SIZE = 100

#: Максимум элементов в одном паке (ограничение Telegram).
MAX_TILES_PER_PACK = 200

#: Разумные границы числа колонок. Больше 12 нечитаемо на телефоне,
#: меньше 1 бессмысленно.
MIN_COLS = 1
MAX_COLS = 12

#: К какому количеству тайлов стремится автоподбор сетки.
#: 36 = сетка 6x6, визуально плотная картинка, которая ещё не режет глаз в чате.
AUTO_TARGET_TILES = 36

#: Допустимый диапазон паддинга в долях тайла.
#: Отрицательный паддинг зумит содержимое (тайлы наезжают друг на друга и стык
#: пропадает), положительный — сжимает и оставляет прозрачные поля.
MIN_PADDING = -0.20
MAX_PADDING = 0.20


class GridError(ValueError):
    """Сетку построить невозможно — например, на вход пришёл нулевой размер."""


@dataclass(frozen=True)
class GridPlan:
    """Готовый план нарезки: во что превращаем исходник."""

    cols: int
    rows: int
    tile: int = TILE_SIZE

    @property
    def total(self) -> int:
        """Сколько эмодзи окажется в паке."""
        return self.cols * self.rows

    @property
    def canvas(self) -> tuple[int, int]:
        """Размер холста, к которому приводим исходник перед резкой."""
        return self.cols * self.tile, self.rows * self.tile

    def tile_box(self, index: int) -> tuple[int, int, int, int]:
        """Координаты тайла по его порядковому номеру (слева направо, сверху вниз).

        Возвращает (left, upper, right, lower) — в том виде, в каком их ждёт
        Pillow.crop и ffmpeg crop.
        """
        if not 0 <= index < self.total:
            raise IndexError(f"тайл {index} вне сетки {self.cols}x{self.rows}")
        row, col = divmod(index, self.cols)
        left = col * self.tile
        upper = row * self.tile
        return left, upper, left + self.tile, upper + self.tile

    def __str__(self) -> str:
        return f"{self.cols}x{self.rows} ({self.total} эмодзи)"


def clamp_padding(padding: float) -> float:
    """Загоняет паддинг в допустимый диапазон вместо того, чтобы падать."""
    return max(MIN_PADDING, min(MAX_PADDING, padding))


def rows_for_cols(width: int, height: int, cols: int) -> int:
    """Сколько рядов нужно, чтобы тайлы остались квадратными при заданных колонках."""
    tile_width = width / cols
    return max(1, round(height / tile_width))


def plan_grid(
    width: int,
    height: int,
    cols: int | None = None,
    *,
    max_tiles: int = MAX_TILES_PER_PACK,
) -> GridPlan:
    """Строит план нарезки по размеру исходника.

    cols=None включает автоподбор: выбираем число колонок, при котором картинка
    ближе всего к AUTO_TARGET_TILES и при этом влезает в лимит пака.
    """
    if width <= 0 or height <= 0:
        raise GridError(f"некорректный размер исходника: {width}x{height}")
    if max_tiles < 1:
        raise GridError(f"лимит тайлов должен быть положительным, получено {max_tiles}")

    if cols is None:
        return _auto_plan(width, height, max_tiles)

    cols = max(MIN_COLS, min(MAX_COLS, cols))
    rows = rows_for_cols(width, height, cols)

    # Пак переполнен — ужимаем сетку, сохраняя пропорции, пока не влезет.
    while cols * rows > max_tiles:
        if cols > MIN_COLS:
            cols -= 1
            rows = rows_for_cols(width, height, cols)
        elif rows > 1:
            rows = min(rows, max_tiles)
        else:
            break

    if cols * rows > max_tiles:
        raise GridError(
            f"сетка {cols}x{rows} не помещается в лимит {max_tiles} эмодзи"
        )
    return GridPlan(cols=cols, rows=rows)


def _auto_plan(width: int, height: int, max_tiles: int) -> GridPlan:
    """Перебирает все допустимые колонки и берёт ближайшую к целевому объёму."""
    best: GridPlan | None = None
    best_score: float | None = None

    for cols in range(MIN_COLS, MAX_COLS + 1):
        rows = rows_for_cols(width, height, cols)
        total = cols * rows
        if total > max_tiles:
            continue
        # Штрафуем отклонение от цели и заодно вырожденные полоски 1xN.
        score = abs(total - AUTO_TARGET_TILES) + _degeneracy_penalty(cols, rows)
        if best_score is None or score < best_score:
            best, best_score = GridPlan(cols=cols, rows=rows), score

    if best is None:
        # Картинка настолько вытянутая, что даже одна колонка не влезает.
        fallback_rows = min(max_tiles, max(1, rows_for_cols(width, height, MIN_COLS)))
        best = GridPlan(cols=MIN_COLS, rows=fallback_rows)
    return best


def _degeneracy_penalty(cols: int, rows: int) -> float:
    """Мягко отговаривает автоподбор от сеток-полосок вроде 1x20."""
    if cols == 1 or rows == 1:
        return 12.0
    return 0.0


def estimate_tiles(width: int, height: int, cols: int) -> int:
    """Сколько эмодзи получится при такой сетке — для подписи на кнопке."""
    return cols * rows_for_cols(width, height, cols)


def fit_canvas(
    width: int, height: int, plan: GridPlan, mode: str = "cover"
) -> tuple[int, int]:
    """Размер, до которого масштабируем исходник перед укладкой на холст.

    ``cover`` заполняет холст целиком и обрезает лишнее — картинка без полей.
    ``contain`` вписывает целиком и оставляет прозрачные поля — ничего не теряется.
    """
    canvas_w, canvas_h = plan.canvas
    scale_w = canvas_w / width
    scale_h = canvas_h / height
    scale = max(scale_w, scale_h) if mode == "cover" else min(scale_w, scale_h)
    return max(1, ceil(width * scale)), max(1, ceil(height * scale))
