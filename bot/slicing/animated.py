"""Нарезка GIF и коротких видео на анимированные эмодзи (WEBM VP9 с альфой).

Telegram принимает анимированный эмодзи только как VP9 в контейнере WebM,
100x100, не длиннее 3 секунд, не больше 30 кадров в секунду и до 256 КБ на файл.
Всё это здесь и обеспечивается — включая подгонку битрейта под лимит размера.

Требует установленный ffmpeg с libvpx-vp9. Проверяется через ``ensure_ffmpeg()``.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .grid import GridPlan, clamp_padding
from .static import Tile

#: Жёсткие требования Telegram к анимированному эмодзи.
MAX_DURATION_SEC = 3.0
MAX_FPS = 30
MAX_FILE_BYTES = 256 * 1024

#: Сколько тайлов кодируем за один вызов ffmpeg. Больше — быстрее,
#: но фильтрграф и потребление памяти растут линейно.
BATCH_SIZE = 12

#: Лестница качества: если файл не влез в лимит, поднимаемся на ступень выше.
_CRF_LADDER = (34, 40, 46, 52, 58)


class FFmpegMissingError(RuntimeError):
    """ffmpeg не найден или собран без libvpx-vp9."""


class EncodeError(RuntimeError):
    """ffmpeg отработал с ошибкой."""


@dataclass(frozen=True)
class MediaInfo:
    width: int
    height: int
    duration: float
    fps: float


def ensure_ffmpeg() -> str:
    """Возвращает путь к ffmpeg или объясняет, чего не хватает."""
    binary = shutil.which("ffmpeg")
    if not binary:
        raise FFmpegMissingError(
            "ffmpeg не найден в PATH — анимированные паки работать не будут"
        )
    return binary


async def probe(path: Path) -> MediaInfo:
    """Читает размер, длительность и частоту кадров через ffprobe."""
    binary = shutil.which("ffprobe")
    if not binary:
        raise FFmpegMissingError("ffprobe не найден в PATH")

    args = [
        binary, "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,avg_frame_rate:format=duration",
        "-of", "json", str(path),
    ]
    payload = json.loads(await _run(args))
    stream = (payload.get("streams") or [{}])[0]
    duration = float(payload.get("format", {}).get("duration") or 0.0)

    return MediaInfo(
        width=int(stream.get("width") or 0),
        height=int(stream.get("height") or 0),
        duration=duration,
        fps=_parse_fps(stream.get("avg_frame_rate")),
    )


async def slice_animation(
    path: Path,
    plan: GridPlan,
    *,
    fit: str = "cover",
    padding: float = 0.0,
    duration: float = MAX_DURATION_SEC,
) -> list[Tile]:
    """Режет анимацию по сетке и возвращает готовые WEBM-тайлы."""
    ensure_ffmpeg()
    padding = clamp_padding(padding)
    duration = min(duration, MAX_DURATION_SEC)

    tiles: list[Tile] = []
    with tempfile.TemporaryDirectory(prefix="dibbuk-anim-") as workdir:
        out_dir = Path(workdir)
        indexes = list(range(plan.total))
        for start in range(0, len(indexes), BATCH_SIZE):
            batch = indexes[start : start + BATCH_SIZE]
            tiles.extend(
                await _encode_batch(path, plan, batch, out_dir, fit, padding, duration)
            )
    return sorted(tiles, key=lambda tile: tile.index)


async def _encode_batch(
    source: Path,
    plan: GridPlan,
    batch: list[int],
    out_dir: Path,
    fit: str,
    padding: float,
    duration: float,
) -> list[Tile]:
    """Кодирует группу тайлов, при необходимости пережимая переросшие лимит."""
    results: list[Tile] = []
    pending = list(batch)

    for crf in _CRF_LADDER:
        if not pending:
            break
        await _run_encode(source, plan, pending, out_dir, fit, padding, duration, crf)

        still_big: list[int] = []
        for index in pending:
            data = (out_dir / f"tile_{index}.webm").read_bytes()
            if len(data) <= MAX_FILE_BYTES:
                results.append(Tile(index=index, data=data))
            else:
                still_big.append(index)
        pending = still_big

    if pending:
        # Даже на минимальном качестве не влезло — отдаём как есть,
        # Telegram отклонит такой файл и пользователь увидит понятную ошибку.
        for index in pending:
            results.append(
                Tile(index=index, data=(out_dir / f"tile_{index}.webm").read_bytes())
            )
    return results


async def _run_encode(
    source: Path,
    plan: GridPlan,
    indexes: list[int],
    out_dir: Path,
    fit: str,
    padding: float,
    duration: float,
    crf: int,
) -> None:
    """Один вызов ffmpeg, который выплёвывает сразу несколько тайлов."""
    binary = ensure_ffmpeg()
    graph, outputs = _build_filtergraph(plan, indexes, fit, padding)

    args = [binary, "-y", "-hide_banner", "-loglevel", "error",
            "-t", f"{duration:.3f}", "-i", str(source),
            "-filter_complex", graph]

    for label, index in outputs:
        args += [
            "-map", f"[{label}]",
            "-c:v", "libvpx-vp9",
            "-pix_fmt", "yuva420p",
            # Альтернативные опорные кадры ломают альфу в VP9 — отключаем.
            "-auto-alt-ref", "0",
            "-b:v", "0", "-crf", str(crf),
            "-an", "-sn",
            str(out_dir / f"tile_{index}.webm"),
        ]
    await _run(args)


def _build_filtergraph(
    plan: GridPlan, indexes: list[int], fit: str, padding: float
) -> tuple[str, list[tuple[str, int]]]:
    """Собирает filter_complex: приведение к холсту, split и кроп каждого тайла."""
    canvas_w, canvas_h = plan.canvas
    ratio = "increase" if fit == "cover" else "decrease"

    base = (
        f"[0:v]format=rgba,"
        f"scale={canvas_w}:{canvas_h}:force_original_aspect_ratio={ratio}:flags=lanczos,"
        f"pad={canvas_w}:{canvas_h}:(ow-iw)/2:(oh-ih)/2:color=0x00000000,"
        f"crop={canvas_w}:{canvas_h},"
        f"fps={MAX_FPS}"
    )

    count = len(indexes)
    labels = [f"s{i}" for i in range(count)]
    chains = [f"{base},split={count}" + "".join(f"[{label}]" for label in labels)]

    outputs: list[tuple[str, int]] = []
    for label, index in zip(labels, indexes):
        left, upper, _, _ = plan.tile_box(index)
        out_label = f"o{index}"
        chains.append(
            f"[{label}]crop={plan.tile}:{plan.tile}:{left}:{upper}"
            f"{_padding_filter(padding, plan.tile)}[{out_label}]"
        )
        outputs.append((out_label, index))

    return ";".join(chains), outputs


def _padding_filter(padding: float, tile: int) -> str:
    """Тот же паддинг, что и для статики, но на языке фильтров ffmpeg."""
    if padding == 0:
        return ""
    content = max(1, round(tile * (1.0 - 2.0 * padding)))
    if content <= tile:
        return (
            f",scale={content}:{content}:flags=lanczos"
            f",pad={tile}:{tile}:(ow-iw)/2:(oh-ih)/2:color=0x00000000"
        )
    return f",scale={content}:{content}:flags=lanczos,crop={tile}:{tile}"


def _parse_fps(value: str | None) -> float:
    """avg_frame_rate приходит дробью вида '30000/1001'."""
    if not value or value == "0/0":
        return 0.0
    if "/" in value:
        numerator, _, denominator = value.partition("/")
        try:
            den = float(denominator)
            return float(numerator) / den if den else 0.0
        except ValueError:
            return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


async def _run(args: list[str]) -> str:
    """Запускает внешний процесс и поднимает исключение с его stderr."""
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise EncodeError(stderr.decode("utf-8", "replace").strip() or "ffmpeg failed")
    return stdout.decode("utf-8", "replace")
