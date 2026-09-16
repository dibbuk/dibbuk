"""Нарезка статичной картинки на тайлы 100x100 в формате PNG.

Здесь же живёт удаление фона. Оно намеренно сделано без тяжёлых нейросетевых
зависимостей: заливкой от краёв снимается однотонный и градиентный фон,
а это подавляющее большинство мемов, логотипов и скриншотов, которые несут в бота.
"""

from __future__ import annotations

import io
from collections import deque
from dataclasses import dataclass

from PIL import Image, ImageFilter

from .grid import GridPlan, clamp_padding, fit_canvas

#: Ниже этого значения альфа считается полной прозрачностью.
_ALPHA_CUTOFF = 8

#: Насколько далеко от цвета угла пиксель ещё считается фоном (евклид по RGB).
_DEFAULT_BG_TOLERANCE = 32.0


class SliceError(ValueError):
    """Исходник не удалось разобрать как картинку."""


@dataclass(frozen=True)
class Tile:
    """Один готовый элемент будущего пака."""

    index: int
    data: bytes
    #: Базовый эмодзи-триггер, который Telegram предложит для этого тайла.
    emoji: str = "🔳"

    @property
    def size(self) -> int:
        return len(self.data)


def load_image(data: bytes) -> Image.Image:
    """Открывает картинку и приводит её к RGBA, не теряя прозрачность."""
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as exc:  # noqa: BLE001 — Pillow бросает разнородные исключения
        raise SliceError(f"не удалось прочитать изображение: {exc}") from exc
    return image.convert("RGBA")


def slice_image(
    source: bytes | Image.Image,
    plan: GridPlan,
    *,
    fit: str = "cover",
    padding: float = 0.0,
    drop_background: bool = False,
    skip_empty: bool = True,
) -> list[Tile]:
    """Режет картинку по плану сетки и возвращает готовые PNG-тайлы.

    ``skip_empty`` выбрасывает полностью прозрачные тайлы — после удаления фона
    их бывает много по углам, и они только засоряют пак.
    """
    image = source if isinstance(source, Image.Image) else load_image(source)
    if drop_background:
        image = remove_background(image)

    canvas = _compose_canvas(image, plan, fit)
    padding = clamp_padding(padding)

    tiles: list[Tile] = []
    for index in range(plan.total):
        tile_image = canvas.crop(plan.tile_box(index))
        tile_image = _apply_padding(tile_image, padding, plan.tile)
        if skip_empty and _is_blank(tile_image):
            continue
        tiles.append(Tile(index=index, data=_encode_png(tile_image)))
    return tiles


def _compose_canvas(image: Image.Image, plan: GridPlan, fit: str) -> Image.Image:
    """Масштабирует исходник и центрирует его на прозрачном холсте сетки."""
    target = fit_canvas(image.width, image.height, plan, fit)
    resized = image.resize(target, Image.LANCZOS)

    canvas = Image.new("RGBA", plan.canvas, (0, 0, 0, 0))
    offset = (
        (plan.canvas[0] - resized.width) // 2,
        (plan.canvas[1] - resized.height) // 2,
    )
    canvas.paste(resized, offset)
    return canvas


def _apply_padding(tile: Image.Image, padding: float, tile_size: int) -> Image.Image:
    """Сжимает или зумит содержимое тайла внутри его 100x100 рамки.

    Положительный паддинг оставляет прозрачные поля — картинка в чате выглядит
    разреженной. Отрицательный наоборот зумит, и тайлы сходятся встык на тех
    клиентах, где Telegram добавляет зазор между эмодзи. Именно эта настройка
    решает проблему «на айфоне картинка не сходится».
    """
    if padding == 0:
        return tile

    scale = 1.0 - 2.0 * padding
    content = max(1, round(tile_size * scale))
    resized = tile.resize((content, content), Image.LANCZOS)

    if content <= tile_size:
        result = Image.new("RGBA", (tile_size, tile_size), (0, 0, 0, 0))
        offset = (tile_size - content) // 2
        result.paste(resized, (offset, offset))
        return result

    # Содержимое переросло рамку — обрезаем по центру.
    crop = (content - tile_size) // 2
    return resized.crop((crop, crop, crop + tile_size, crop + tile_size))


def _is_blank(tile: Image.Image) -> bool:
    """Тайл целиком прозрачный и в пак его класть незачем."""
    alpha = tile.getchannel("A")
    return (alpha.getextrema() or (0, 0))[1] < _ALPHA_CUTOFF


def _encode_png(tile: Image.Image) -> bytes:
    buffer = io.BytesIO()
    tile.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def remove_background(
    image: Image.Image, tolerance: float = _DEFAULT_BG_TOLERANCE
) -> Image.Image:
    """Снимает однотонный фон заливкой от краёв картинки.

    Заливка идёт от всех граничных пикселей внутрь и останавливается там, где
    цвет отходит от фонового дальше, чем на ``tolerance``. В отличие от простой
    замены цвета, это не выбивает дыры внутри объекта, если он совпал с фоном.
    """
    rgba = image.convert("RGBA")
    width, height = rgba.size
    if width * height == 0:
        return rgba

    pixels = rgba.load()
    reference = _edge_reference_color(pixels, width, height)
    if reference is None:
        return rgba

    tolerance_sq = tolerance * tolerance
    visited = bytearray(width * height)
    queue: deque[tuple[int, int]] = deque()

    for x in range(width):
        queue.append((x, 0))
        queue.append((x, height - 1))
    for y in range(height):
        queue.append((0, y))
        queue.append((width - 1, y))

    while queue:
        x, y = queue.popleft()
        if not (0 <= x < width and 0 <= y < height):
            continue
        flat = y * width + x
        if visited[flat]:
            continue
        visited[flat] = 1

        r, g, b, a = pixels[x, y]
        if a < _ALPHA_CUTOFF:
            queue.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))
            continue
        if _distance_sq((r, g, b), reference) > tolerance_sq:
            continue

        pixels[x, y] = (r, g, b, 0)
        queue.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))

    return _soften_alpha(rgba)


def _edge_reference_color(pixels, width: int, height: int) -> tuple[int, int, int] | None:
    """Усредняет углы — это и есть предполагаемый цвет фона."""
    corners = [
        (0, 0),
        (width - 1, 0),
        (0, height - 1),
        (width - 1, height - 1),
    ]
    samples = []
    for x, y in corners:
        r, g, b, a = pixels[x, y]
        if a >= _ALPHA_CUTOFF:
            samples.append((r, g, b))
    if not samples:
        return None
    count = len(samples)
    return (
        sum(s[0] for s in samples) // count,
        sum(s[1] for s in samples) // count,
        sum(s[2] for s in samples) // count,
    )


def _distance_sq(left: tuple[int, int, int], right: tuple[int, int, int]) -> float:
    return sum((a - b) ** 2 for a, b in zip(left, right))


def _soften_alpha(image: Image.Image) -> Image.Image:
    """Слегка размывает границу альфы, чтобы края не выглядели рваными."""
    alpha = image.getchannel("A").filter(ImageFilter.GaussianBlur(0.6))
    image.putalpha(alpha)
    return image
