"""Обработчики Telegram: приём файла, панель настроек, нарезка, оплата."""

from __future__ import annotations

import logging
import tempfile
from datetime import date, timedelta
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    LabeledPrice,
    Message,
    MessageEntity,
    PreCheckoutQuery,
)

from . import texts
from .config import Settings
from .keyboards import background_label, fit_label, settings_panel, shift_cols
from .mosaic import mosaic_fits_message
from .packs import PackError, build_pack_title, create_emoji_pack
from .slicing.animated import FFmpegMissingError, probe, slice_animation
from .slicing.grid import GridError, estimate_tiles, plan_grid
from .slicing.static import SliceError, load_image, slice_image
from .storage import PendingJob, Storage, UserState

log = logging.getLogger(__name__)
router = Router(name="dibbuk")

#: Payload-префиксы счетов в Telegram Stars.
PAYLOAD_UNBRAND = "unbrand"
PAYLOAD_SUBSCRIPTION = "subscription"

#: Сколько дней даёт одна покупка подписки.
SUBSCRIPTION_DAYS = 30

#: Шаг изменения полей одной кнопкой.
PADDING_STEP = 0.02


# --------------------------------------------------------------------------- #
# Команды
# --------------------------------------------------------------------------- #

@router.message(CommandStart())
async def on_start(message: Message) -> None:
    await message.answer(texts.START)


@router.message(Command("help"))
async def on_help(message: Message) -> None:
    await message.answer(texts.HELP)


@router.message(Command("buy"))
async def on_buy(message: Message, settings: Settings) -> None:
    await message.answer(
        texts.BUY.format(
            limit=settings.free_daily_limit,
            unbrand=settings.stars_price_unbrand,
            subscription=settings.stars_price_subscription,
        )
    )
    await message.answer_invoice(
        title="Пак без ссылки на бота",
        description="Снимает ссылку на бота из названия одного пака.",
        payload=PAYLOAD_UNBRAND,
        currency="XTR",
        prices=[
            LabeledPrice(label="Без ссылки", amount=settings.stars_price_unbrand)
        ],
    )
    await message.answer_invoice(
        title="Подписка на месяц",
        description="Без суточных лимитов, без ссылки в названии, приоритет в очереди.",
        payload=PAYLOAD_SUBSCRIPTION,
        currency="XTR",
        prices=[
            LabeledPrice(label="30 дней", amount=settings.stars_price_subscription)
        ],
    )


# --------------------------------------------------------------------------- #
# Оплата
# --------------------------------------------------------------------------- #

@router.pre_checkout_query()
async def on_pre_checkout(query: PreCheckoutQuery) -> None:
    # Товар цифровой и выдаётся сразу — подтверждаем всё, что дошло сюда.
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def on_paid(message: Message, app_storage: Storage) -> None:
    payment = message.successful_payment
    state = app_storage.get(message.from_user.id)

    if payment.invoice_payload == PAYLOAD_SUBSCRIPTION:
        base = max(state.subscription_until or date.today(), date.today())
        state.subscription_until = base + timedelta(days=SUBSCRIPTION_DAYS)
        await message.answer(
            texts.PAID_SUBSCRIPTION.format(
                until=state.subscription_until.strftime("%d.%m.%Y")
            )
        )
        return

    state.unbrand_credits += 1
    await message.answer(texts.PAID_UNBRAND)


# --------------------------------------------------------------------------- #
# Приём исходника
# --------------------------------------------------------------------------- #

@router.message(F.photo | F.document | F.animation | F.video | F.sticker)
async def on_media(
    message: Message, bot: Bot, app_storage: Storage, settings: Settings
) -> None:
    media = _describe_media(message)
    if media is None:
        await message.answer(texts.UNSUPPORTED)
        return

    file_id, is_animated, width, height, size = media

    if size and size > settings.max_input_bytes:
        await message.answer(
            texts.TOO_BIG.format(limit=settings.max_input_bytes // (1024 * 1024))
        )
        return
    if is_animated and not settings.enable_animation:
        await message.answer(texts.ANIMATION_DISABLED)
        return

    app_storage.reset_job(message.from_user.id)
    path = await _download(bot, file_id, animated=is_animated)

    try:
        width, height, is_animated = await _resolve_dimensions(
            path, width, height, is_animated
        )
    except (SliceError, FFmpegMissingError) as exc:
        path.unlink(missing_ok=True)
        await message.answer(texts.ERROR_GENERIC.format(reason=exc))
        return

    state = app_storage.get(message.from_user.id)
    state.job = PendingJob(
        path=path, is_animated=is_animated, width=width, height=height
    )
    panel = await message.answer(
        _panel_text(state), reply_markup=_panel_markup(state)
    )
    state.job.panel_message_id = panel.message_id


# --------------------------------------------------------------------------- #
# Панель настроек
# --------------------------------------------------------------------------- #

@router.callback_query(F.data.startswith("cols:"))
async def on_cols(query: CallbackQuery, app_storage: Storage) -> None:
    state = app_storage.get(query.from_user.id)
    if state.job is None:
        await query.answer(texts.JOB_LOST, show_alert=True)
        return

    action = query.data.split(":", 1)[1]
    if action == "auto":
        state.settings.cols = None
    else:
        current = _current_cols(state)
        state.settings.cols = shift_cols(current, 1 if action == "+1" else -1)

    await _refresh_panel(query, state)


@router.callback_query(F.data.startswith("pad:"))
async def on_padding(query: CallbackQuery, app_storage: Storage) -> None:
    state = app_storage.get(query.from_user.id)
    if state.job is None:
        await query.answer(texts.JOB_LOST, show_alert=True)
        return

    action = query.data.split(":", 1)[1]
    if action == "0":
        state.settings.padding = 0.0
    else:
        state.settings.with_padding(
            PADDING_STEP if action == "+1" else -PADDING_STEP
        )

    await _refresh_panel(query, state)


@router.callback_query(F.data == "fit:toggle")
async def on_fit(query: CallbackQuery, app_storage: Storage) -> None:
    state = app_storage.get(query.from_user.id)
    if state.job is None:
        await query.answer(texts.JOB_LOST, show_alert=True)
        return
    state.settings.fit = "contain" if state.settings.fit == "cover" else "cover"
    await _refresh_panel(query, state)


@router.callback_query(F.data == "bg:toggle")
async def on_background(query: CallbackQuery, app_storage: Storage) -> None:
    state = app_storage.get(query.from_user.id)
    if state.job is None:
        await query.answer(texts.JOB_LOST, show_alert=True)
        return
    state.settings.drop_background = not state.settings.drop_background
    await _refresh_panel(query, state)


# --------------------------------------------------------------------------- #
# Нарезка
# --------------------------------------------------------------------------- #

@router.callback_query(F.data == "run")
async def on_run(
    query: CallbackQuery, bot: Bot, app_storage: Storage, settings: Settings
) -> None:
    state = app_storage.get(query.from_user.id)
    job = state.job
    if job is None:
        await query.answer(texts.JOB_LOST, show_alert=True)
        return

    if not state.is_subscribed and state.quota_left(settings.free_daily_limit) <= 0:
        await query.answer(
            texts.QUOTA_SPENT.format(limit=settings.free_daily_limit), show_alert=True
        )
        return

    await query.answer()
    status = await query.message.answer(texts.PROCESSING)

    try:
        plan = plan_grid(job.width, job.height, state.settings.cols)
        tiles = await _cut(job, plan, state)
        await status.edit_text(texts.CREATING_PACK)

        me = await bot.me()
        title = build_pack_title(
            _default_title(query),
            me.username or "bot",
            branded=state.should_brand(),
            suffix=settings.branding_suffix,
        )
        pack = await create_emoji_pack(
            bot,
            user_id=query.from_user.id,
            title=title,
            tiles=tiles,
            animated=job.is_animated,
            bot_username=me.username or "bot",
        )
    except (GridError, SliceError, PackError, FFmpegMissingError) as exc:
        log.warning("нарезка не удалась для %s: %s", query.from_user.id, exc)
        await status.edit_text(texts.ERROR_GENERIC.format(reason=exc))
        return
    except Exception:  # noqa: BLE001 — пользователь не должен видеть трейсбек
        log.exception("необработанная ошибка нарезки для %s", query.from_user.id)
        await status.edit_text(
            texts.ERROR_GENERIC.format(reason="внутренняя ошибка, попробуйте ещё раз")
        )
        return

    # Пак создан — только теперь списываем квоту и оплаченную разблокировку.
    state.packs_today += 1
    if not state.should_brand():
        state.consume_unbrand()

    body = texts.RESULT.format(total=len(tiles), link=pack.link)
    if state.should_brand():
        body += texts.RESULT_BRANDED
    await status.edit_text(body, disable_web_page_preview=True)

    await _send_mosaic(query, pack, plan)
    app_storage.reset_job(query.from_user.id)


async def _send_mosaic(query: CallbackQuery, pack, plan) -> None:
    """Отправляет картинку, собранную из кастомных эмодзи прямо в тексте."""
    mosaic = pack.mosaic(plan)
    if mosaic.is_empty:
        return
    if not mosaic_fits_message(mosaic):
        await query.message.answer(texts.MOSAIC_TOO_LONG)
        return

    entities = [
        MessageEntity(
            type="custom_emoji",
            offset=item.offset,
            length=item.length,
            custom_emoji_id=item.custom_emoji_id,
        )
        for item in mosaic.entities
    ]
    try:
        await query.message.answer(mosaic.text, entities=entities, parse_mode=None)
    except Exception:  # noqa: BLE001 — мозаика необязательна, пак уже отдан
        log.warning("не удалось отправить мозаику", exc_info=True)


# --------------------------------------------------------------------------- #
# Вспомогательное
# --------------------------------------------------------------------------- #

def _describe_media(message: Message) -> tuple[str, bool, int, int, int] | None:
    """Достаёт из сообщения file_id, признак анимации и размеры, если они известны."""
    if message.photo:
        photo = message.photo[-1]
        return photo.file_id, False, photo.width, photo.height, photo.file_size or 0
    if message.animation:
        item = message.animation
        return item.file_id, True, item.width, item.height, item.file_size or 0
    if message.video:
        item = message.video
        return item.file_id, True, item.width, item.height, item.file_size or 0
    if message.sticker:
        item = message.sticker
        animated = bool(item.is_video or item.is_animated)
        return item.file_id, animated, item.width, item.height, item.file_size or 0
    if message.document:
        item = message.document
        mime = (item.mime_type or "").lower()
        if mime.startswith("video/") or mime == "image/gif":
            return item.file_id, True, 0, 0, item.file_size or 0
        if mime.startswith("image/"):
            return item.file_id, False, 0, 0, item.file_size or 0
    return None


async def _download(bot: Bot, file_id: str, *, animated: bool) -> Path:
    """Скачивает исходник во временный файл и возвращает путь к нему."""
    suffix = ".bin" if animated else ".img"
    handle = tempfile.NamedTemporaryFile(
        prefix="dibbuk-src-", suffix=suffix, delete=False
    )
    handle.close()
    path = Path(handle.name)
    await bot.download(file_id, destination=path)
    return path


async def _resolve_dimensions(
    path: Path, width: int, height: int, is_animated: bool
) -> tuple[int, int, bool]:
    """Дополняет размеры, если Telegram их не прислал (типично для документов)."""
    if width > 0 and height > 0:
        return width, height, is_animated

    if is_animated:
        info = await probe(path)
        if info.width and info.height:
            return info.width, info.height, True
        raise SliceError("не удалось определить размер видео")

    image = load_image(path.read_bytes())
    return image.width, image.height, False


async def _cut(job: PendingJob, plan, state: UserState):
    """Вызывает подходящий резчик — статичный или анимированный."""
    if job.is_animated:
        return await slice_animation(
            job.path,
            plan,
            fit=state.settings.fit,
            padding=state.settings.padding,
        )
    return slice_image(
        job.path.read_bytes(),
        plan,
        fit=state.settings.fit,
        padding=state.settings.padding,
        drop_background=state.settings.drop_background,
    )


def _current_cols(state: UserState) -> int:
    """Текущее число колонок: заданное пользователем или подобранное автоматически."""
    if state.settings.cols is not None:
        return state.settings.cols
    job = state.job
    return plan_grid(job.width, job.height).cols if job else 6


def _panel_text(state: UserState) -> str:
    job = state.job
    cols = _current_cols(state)
    plan = plan_grid(job.width, job.height, state.settings.cols)
    return texts.PANEL.format(
        width=job.width,
        height=job.height,
        kind=", анимация" if job.is_animated else "",
        cols=plan.cols,
        rows=plan.rows,
        total=plan.total,
        padding=state.settings.padding_label,
        fit=fit_label(state.settings.fit),
        background=background_label(state.settings.drop_background),
    )


def _panel_markup(state: UserState):
    plan = plan_grid(state.job.width, state.job.height, state.settings.cols)
    return settings_panel(state.settings, plan.cols)


async def _refresh_panel(query: CallbackQuery, state: UserState) -> None:
    """Перерисовывает панель на месте, не плодя сообщений."""
    await query.answer()
    try:
        await query.message.edit_text(
            _panel_text(state), reply_markup=_panel_markup(state)
        )
    except Exception:  # noqa: BLE001 — «message is not modified» не ошибка
        log.debug("панель не изменилась", exc_info=True)


def _default_title(query: CallbackQuery) -> str:
    """Название пака по умолчанию — по имени пользователя."""
    name = (query.from_user.first_name or "").strip()
    return f"Пак {name}".strip() if name else "Emoji pack"


__all__ = ["router", "estimate_tiles"]
