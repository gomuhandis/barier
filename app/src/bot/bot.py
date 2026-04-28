"""Telegram bot runner — phone-authenticated, alert-on-press UX.

UX rules (per latest product requirements):

  * `/start` is the only place we ever send the inline keyboard.
  * Pressing a button does NOT spawn a new keyboard message — we just answer
    the callback with `show_alert=True`, so the user sees a popup with the
    success / error text. The original keyboard stays usable on its message.
  * The welcome message tells the user: if the buttons stop working (i.e.
    the message scrolled out of view or was deleted), press /start again.
  * The keyboard layout adapts to permissions:
      - `can_control_barrier` → 🟢 Ochish / 🔴 Yopish row
      - `can_view_own_events` AND user has linked plates → 📋 Mening loglarim
      - always: ℹ️ Holat
  * If the phone is registered but no plate is linked yet, the user is told
    to contact the admin — barrier control is hidden.
  * `Mening loglarim` shows the user's own open/close history plus the
    entry/exit events for the cars linked to their phone.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.config import get_settings
from src.database import SessionLocal
from src.isapi.registry import registry
from src.models import (
    BarrierAction,
    BarrierActionLog,
    BarrierActor,
    EntryExitLog,
    PlatePhoneLink,
    TelegramUser,
)
from src.scheduler.alternating_close import scheduler as alt_close_scheduler
from src.services.user_logs_export import build_user_logs_xlsx
from src.utils.tz import (
    fmt_local,
    local_day_range,
    local_month_range,
    local_year_range,
    now_local,
)

logger = logging.getLogger(__name__)

router = Router()


# ---------------------- helpers ----------------------
def _normalize_phone(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return ""
    if not raw.startswith("+"):
        raw = "+" + re.sub(r"\D", "", raw)
    else:
        raw = "+" + re.sub(r"\D", "", raw[1:])
    return raw


async def _resolve_user(chat_id: int) -> TelegramUser | None:
    async with SessionLocal() as session:
        return (
            await session.scalars(
                select(TelegramUser)
                .options(selectinload(TelegramUser.links).selectinload(PlatePhoneLink.plate))
                .where(TelegramUser.telegram_chat_id == chat_id)
            )
        ).first()


def _menu_for(user: TelegramUser) -> InlineKeyboardMarkup:
    """Build the keyboard layout based on what this user is allowed to do.

    Telegram's Bot API does not expose a way to set a per-button background
    colour, so we fake red / green by repeating 🟩 and 🟥 emoji squares
    throughout the button label.
    """
    rows: list[list[InlineKeyboardButton]] = []
    has_plates = bool(user.links)

    if user.can_control_barrier and has_plates:
        rows.append(
            [
                InlineKeyboardButton(
                    text="OCHISH", callback_data="barrier:open",
                    style="success"
                ),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="YOPISH", callback_data="barrier:close",
                    style="danger"
                ),
            ]
        )
    if user.can_view_own_events and has_plates:
        rows.append(
            [InlineKeyboardButton(text="📋 Mening loglarim", callback_data="me:logs", style="primary")]
        )
        rows.append(
            [InlineKeyboardButton(text="📥 Excel yuklab olish", callback_data="ex:menu", style="primary")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _contact_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Telefon raqamni yuborish", request_contact=True)]
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


_HINT = (
    "\n\n<i>Agar tugmalar ishlamay qolsa, /start ni qayta bosing.</i>"
)


def _welcome_text(user: TelegramUser) -> str:
    plates = ", ".join(link.plate.plate_number for link in user.links) or "—"
    name = user.full_name or user.phone_number
    if not user.links:
        return (
            f"Assalomu alaykum, <b>{name}</b>!\n\n"
            "🚧 <b>PDP University — Parking</b>\n\n"
            "❗ Sizning telefon raqamingizga hali hech qanday mashina raqami "
            "biriktirilmagan.\n"
            "👤 Mashinani botga biriktirish uchun <b>administratorga murojaat qiling</b>."
            + _HINT
        )
    return (
        f"Assalomu alaykum, <b>{name}</b>!\n\n"
        "🚧 <b>PDP University — Parking</b>\n"
        f"🚗 <b>Mashinalaringiz:</b> {plates}\n\n"
        "Quyidagi tugmalar orqali shlakbaumni boshqaring va loglaringizni ko‘ring:"
        + _HINT
    )


# ---------------------- /start ----------------------
@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    user = await _resolve_user(message.chat.id)
    if user is None:
        await message.answer(
            "🚧 <b>PDP University — Parking boti</b>\n\n"
            "Shlakbaumni boshqarish uchun telefon raqamingizni yuboring.\n"
            "<i>Raqam administrator tomonidan ro‘yxatga olingan bo‘lishi kerak.</i>",
            reply_markup=_contact_keyboard(),
        )
        return
    if not user.is_active:
        await message.answer("⛔ Sizning hisobingiz bloklangan. Admin bilan bog‘laning.")
        return
    await message.answer(_welcome_text(user), reply_markup=_menu_for(user))


@router.message(F.contact)
async def on_contact(message: Message) -> None:
    if not message.contact or message.contact.user_id != message.from_user.id:
        await message.answer("Iltimos, o‘zingizning telefon raqamingizni yuboring.")
        return
    phone = _normalize_phone(message.contact.phone_number)
    async with SessionLocal() as session:
        user = (
            await session.scalars(
                select(TelegramUser).where(TelegramUser.phone_number == phone)
            )
        ).first()
        if user is None:
            user = (
                await session.scalars(
                    select(TelegramUser).where(
                        TelegramUser.phone_number.endswith(phone[-9:])
                    )
                )
            ).first()
        if user is None:
            await message.answer(
                f"❌ <b>{phone}</b> raqami ro‘yxatda yo‘q.\n"
                "👤 Admin bilan bog‘laning.",
                reply_markup=ReplyKeyboardRemove(),
            )
            return
        if not user.is_active:
            await message.answer(
                "⛔ Sizning hisobingiz bloklangan.",
                reply_markup=ReplyKeyboardRemove(),
            )
            return
        user.telegram_chat_id = message.chat.id
        user.telegram_username = message.from_user.username
        await session.commit()

    # Re-fetch with relationships eagerly loaded so we can render the menu.
    user = await _resolve_user(message.chat.id)
    assert user is not None
    await message.answer(
        "✅ Ro‘yxatdan o‘tdingiz!", reply_markup=ReplyKeyboardRemove()
    )
    await message.answer(_welcome_text(user), reply_markup=_menu_for(user))


# ---------------------- inline buttons ----------------------
@router.callback_query(F.data.startswith("barrier:"))
async def on_barrier_button(cq: CallbackQuery) -> None:
    action = cq.data.split(":", 1)[1]
    user = await _resolve_user(cq.message.chat.id)
    if user is None:
        await cq.answer("Avval /start orqali ro‘yxatdan o‘ting.", show_alert=True)
        return
    if not user.is_active:
        await cq.answer("Hisobingiz bloklangan.", show_alert=True)
        return

    if action not in {"open", "close"}:
        await cq.answer("Noma'lum amal.", show_alert=True)
        return

    if not user.can_control_barrier:
        await cq.answer(
            "Sizda shlakbaumni boshqarish huquqi yo‘q.\n"
            "👤 Admin bilan bog‘laning.",
            show_alert=True,
        )
        return

    if not user.links:
        await cq.answer(
            "Sizga hali hech qanday mashina biriktirilmagan.\n"
            "👤 Admin bilan bog‘laning.",
            show_alert=True,
        )
        return

    barrier_action = BarrierAction.OPEN if action == "open" else BarrierAction.CLOSE

    any_ok = False
    any_fail = False
    fail_reason: str | None = None
    async with SessionLocal() as session:
        for client in registry.all():
            resp = await client.barrier_control(action)  # type: ignore[arg-type]
            if resp.ok:
                any_ok = True
            else:
                any_fail = True
                if fail_reason is None:
                    fail_reason = resp.message
            session.add(
                BarrierActionLog(
                    camera_name=client.cam.name,
                    action=barrier_action,
                    actor=BarrierActor.TELEGRAM,
                    actor_detail=f"chat={cq.message.chat.id} phone={user.phone_number}",
                    status_code=resp.status_code,
                    status_message=resp.message[:256],
                )
            )
        await session.commit()

    if any_ok and action == "open":
        alt_close_scheduler.notify_opened(
            reason=f"telegram:open chat={cq.message.chat.id} phone={user.phone_number}"
        )

    if any_ok and not any_fail:
        text = "🟢 Shlakbaum ochildi" if action == "open" else "🔴 Shlakbaum yopildi"
    elif any_ok:
        text = (
            f"⚠ Qisman bajarildi: {fail_reason}"
            if fail_reason
            else "⚠ Qisman bajarildi."
        )
    else:
        text = (
            f"❌ Xatolik: {fail_reason}"
            if fail_reason
            else "❌ Xatolik. Qayta urinib ko‘ring."
        )
    # alert popup, never resend the keyboard.
    await cq.answer(text, show_alert=True)


@router.callback_query(F.data == "me:logs")
async def on_my_logs(cq: CallbackQuery) -> None:
    user = await _resolve_user(cq.message.chat.id)
    if user is None:
        await cq.answer("Avval /start orqali ro‘yxatdan o‘ting.", show_alert=True)
        return
    if not user.is_active or not user.can_view_own_events:
        await cq.answer(
            "Sizda loglarni ko‘rish huquqi yo‘q.\n"
            "👤 Admin bilan bog‘laning.",
            show_alert=True,
        )
        return

    plate_numbers = [link.plate.plate_number for link in user.links]
    async with SessionLocal() as session:
        if plate_numbers:
            entry_rows = list(
                (
                    await session.scalars(
                        select(EntryExitLog)
                        .where(EntryExitLog.plate_number.in_(plate_numbers))
                        .order_by(EntryExitLog.event_time.desc())
                        .limit(10)
                    )
                ).all()
            )
        else:
            entry_rows = []
        # Match this user's barrier actions by phone embedded in actor_detail.
        action_rows = list(
            (
                await session.scalars(
                    select(BarrierActionLog)
                    .where(
                        BarrierActionLog.actor == BarrierActor.TELEGRAM,
                        BarrierActionLog.actor_detail.ilike(
                            f"%phone={user.phone_number}%"
                        ),
                    )
                    .order_by(BarrierActionLog.created_at.desc())
                    .limit(10)
                )
            ).all()
        )

    parts: list[str] = []

    if entry_rows:
        parts.append("🚗 <b>Mashina kirish/chiqish (oxirgi 10):</b>")
        for r in entry_rows:
            arrow = "↘ Kirdi" if r.direction.value == "entry" else "↗ Chiqdi"
            allow = "✅" if r.is_allowed else "⛔"
            parts.append(
                f"{allow} {fmt_local(r.event_time, '%Y-%m-%d %H:%M')} — "
                f"<b>{r.plate_number}</b> {arrow} ({r.camera_name})"
            )
    elif plate_numbers:
        parts.append("🚗 Hali kirish/chiqish hodisalari yo‘q.")
    else:
        parts.append(
            "❗ Sizga mashina biriktirilmagan.\n"
            "👤 Admin bilan bog‘laning."
        )

    parts.append("")  # blank line

    if action_rows:
        parts.append("🚧 <b>Shlakbaum bosgan tugmalaringiz (oxirgi 10):</b>")
        for r in action_rows:
            mark = "🟢" if r.action == BarrierAction.OPEN else "🔴"
            ok = "✅" if r.status_code == 200 else "❌"
            parts.append(
                f"{mark} {fmt_local(r.created_at, '%Y-%m-%d %H:%M')} — "
                f"{r.action.value} ({r.camera_name}) {ok}"
            )
    else:
        parts.append("🚧 Shlakbaumni hali ishlatmagansiz.")

    text = "\n".join(parts)
    # Send as a message so it's scrollable, AND attach the main menu so the
    # user can press open/close, view logs again, or download Excel without
    # having to /start over.
    await cq.message.answer(text, reply_markup=_menu_for(user))
    await cq.answer()


# ---------------------- /menu (re-send keyboard manually) ----------------------
@router.message(Command("menu"))
async def cmd_menu(message: Message) -> None:
    user = await _resolve_user(message.chat.id)
    if user is None:
        await message.answer("Avval /start orqali ro‘yxatdan o‘ting.")
        return
    await message.answer(_welcome_text(user), reply_markup=_menu_for(user))


# ---------------------- Excel export picker ----------------------
_MONTH_UZ_SHORT = [
    "Yan", "Fev", "Mar", "Apr", "May", "Iyn",
    "Iyl", "Avg", "Sen", "Okt", "Noy", "Dek",
]


def _ex_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📅 Bugun", callback_data="ex:p:today")],
            [InlineKeyboardButton(text="📅 Kecha", callback_data="ex:p:yesterday")],
            [InlineKeyboardButton(text="📅 Joriy oy", callback_data="ex:p:month")],
            [InlineKeyboardButton(text="📅 Joriy yil", callback_data="ex:p:year")],
            [InlineKeyboardButton(text="🗓 Boshqa sana...", callback_data="ex:y")],
        ]
    )


def _ex_year_kb() -> InlineKeyboardMarkup:
    cy = now_local().year
    years = [cy - 2, cy - 1, cy]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=str(y), callback_data=f"ex:y:{y}") for y in years],
            [InlineKeyboardButton(text="← Orqaga", callback_data="ex:menu")],
        ]
    )


def _ex_month_kb(year: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, 12, 3):
        row = [
            InlineKeyboardButton(
                text=_MONTH_UZ_SHORT[i + j],
                callback_data=f"ex:y:{year}:m:{i + j + 1}",
            )
            for j in range(3)
        ]
        rows.append(row)
    rows.append(
        [InlineKeyboardButton(text="📊 Butun yil", callback_data=f"ex:y:{year}:all")]
    )
    rows.append([InlineKeyboardButton(text="← Orqaga", callback_data="ex:y")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _ex_day_kb(year: int, month: int) -> InlineKeyboardMarkup:
    import calendar

    _, days_in_month = calendar.monthrange(year, month)
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for d in range(1, days_in_month + 1):
        row.append(
            InlineKeyboardButton(
                text=str(d), callback_data=f"ex:y:{year}:m:{month}:d:{d}"
            )
        )
        if len(row) == 7:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(
        [
            InlineKeyboardButton(
                text="📊 Butun oy", callback_data=f"ex:y:{year}:m:{month}:all"
            )
        ]
    )
    rows.append(
        [InlineKeyboardButton(text="← Orqaga", callback_data=f"ex:y:{year}")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _ensure_export_user(cq: CallbackQuery) -> TelegramUser | None:
    user = await _resolve_user(cq.message.chat.id)
    if user is None:
        await cq.answer("Avval /start orqali ro‘yxatdan o‘ting.", show_alert=True)
        return None
    if not user.is_active or not user.can_view_own_events:
        await cq.answer(
            "Sizda loglarni yuklab olish huquqi yo‘q.\n👤 Admin bilan bog‘laning.",
            show_alert=True,
        )
        return None
    if not user.links:
        await cq.answer(
            "Sizga mashina biriktirilmagan.\n👤 Admin bilan bog‘laning.",
            show_alert=True,
        )
        return None
    return user


@router.callback_query(F.data == "ex:menu")
async def ex_menu(cq: CallbackQuery) -> None:
    if await _ensure_export_user(cq) is None:
        return
    await cq.message.edit_text(
        "📥 <b>Excel yuklab olish</b>\n\nVaqt oralig‘ini tanlang:",
        reply_markup=_ex_menu_kb(),
    )
    await cq.answer()


@router.callback_query(F.data == "ex:y")
async def ex_pick_year(cq: CallbackQuery) -> None:
    if await _ensure_export_user(cq) is None:
        return
    await cq.message.edit_text("Yilni tanlang:", reply_markup=_ex_year_kb())
    await cq.answer()


@router.callback_query(F.data.regexp(r"^ex:y:\d+$"))
async def ex_pick_month(cq: CallbackQuery) -> None:
    if await _ensure_export_user(cq) is None:
        return
    year = int(cq.data.split(":")[2])
    await cq.message.edit_text(
        f"<b>{year}</b> yil — oyni tanlang:", reply_markup=_ex_month_kb(year)
    )
    await cq.answer()


@router.callback_query(F.data.regexp(r"^ex:y:\d+:m:\d+$"))
async def ex_pick_day(cq: CallbackQuery) -> None:
    if await _ensure_export_user(cq) is None:
        return
    parts = cq.data.split(":")
    year, month = int(parts[2]), int(parts[4])
    await cq.message.edit_text(
        f"<b>{year}-{month:02d}</b> — kunni tanlang:",
        reply_markup=_ex_day_kb(year, month),
    )
    await cq.answer()


async def _send_user_excel(
    cq: CallbackQuery,
    user: TelegramUser,
    start: datetime,
    end: datetime,
    label: str,
    pretty: str,
) -> None:
    plate_numbers = [link.plate.plate_number for link in user.links]
    async with SessionLocal() as session:
        try:
            data = await build_user_logs_xlsx(
                session,
                user=user,
                plate_numbers=plate_numbers,
                start=start,
                end=end,
                label=pretty,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("user excel build failed")
            await cq.answer(f"❌ Xato: {exc}", show_alert=True)
            return

    safe_label = label.replace(" ", "_").replace("/", "-")
    filename = f"loglarim_{safe_label}.xlsx"
    file = BufferedInputFile(data, filename=filename)
    await cq.message.answer_document(
        file,
        caption=(
            f"📥 <b>Loglaringiz tayyor</b>\n"
            f"🗓 Davr: <b>{pretty}</b>\n"
            f"🚗 Mashinalar: <b>{', '.join(plate_numbers)}</b>"
        ),
    )
    # restore the main menu so the user is not stuck on the picker.
    await cq.message.answer(_welcome_text(user), reply_markup=_menu_for(user))
    await cq.answer("✅ Tayyor")


@router.callback_query(F.data.regexp(r"^ex:p:(today|yesterday|month|year)$"))
async def ex_preset(cq: CallbackQuery) -> None:
    user = await _ensure_export_user(cq)
    if user is None:
        return
    code = cq.data.split(":")[2]
    today = now_local()
    if code == "today":
        s, e = local_day_range(today.year, today.month, today.day)
        label = today.strftime("%Y-%m-%d") + "_bugun"
        pretty = f"Bugun ({today.strftime('%Y-%m-%d')})"
    elif code == "yesterday":
        y = today - timedelta(days=1)
        s, e = local_day_range(y.year, y.month, y.day)
        label = y.strftime("%Y-%m-%d") + "_kecha"
        pretty = f"Kecha ({y.strftime('%Y-%m-%d')})"
    elif code == "month":
        s, e = local_month_range(today.year, today.month)
        label = f"{today.year}-{today.month:02d}_oy"
        pretty = f"Joriy oy ({today.year}-{today.month:02d})"
    else:  # year
        s, e = local_year_range(today.year)
        label = f"{today.year}_yil"
        pretty = f"Joriy yil ({today.year})"
    await _send_user_excel(cq, user, s, e, label, pretty)


@router.callback_query(F.data.regexp(r"^ex:y:\d+:all$"))
async def ex_full_year(cq: CallbackQuery) -> None:
    user = await _ensure_export_user(cq)
    if user is None:
        return
    year = int(cq.data.split(":")[2])
    s, e = local_year_range(year)
    await _send_user_excel(
        cq, user, s, e, f"{year}_yil", f"{year} yil (butun)"
    )


@router.callback_query(F.data.regexp(r"^ex:y:\d+:m:\d+:all$"))
async def ex_full_month(cq: CallbackQuery) -> None:
    user = await _ensure_export_user(cq)
    if user is None:
        return
    parts = cq.data.split(":")
    year, month = int(parts[2]), int(parts[4])
    s, e = local_month_range(year, month)
    await _send_user_excel(
        cq,
        user,
        s,
        e,
        f"{year}-{month:02d}_oy",
        f"{year}-{month:02d} (butun oy)",
    )


@router.callback_query(F.data.regexp(r"^ex:y:\d+:m:\d+:d:\d+$"))
async def ex_specific_day(cq: CallbackQuery) -> None:
    user = await _ensure_export_user(cq)
    if user is None:
        return
    parts = cq.data.split(":")
    year, month, day = int(parts[2]), int(parts[4]), int(parts[6])
    s, e = local_day_range(year, month, day)
    label = f"{year}-{month:02d}-{day:02d}"
    await _send_user_excel(cq, user, s, e, label, label)


# ---------------------- runtime ----------------------
class BotRuntime:
    def __init__(self) -> None:
        self._bot: Bot | None = None
        self._dp: Dispatcher | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        token = get_settings().telegram_bot_token
        if not token:
            logger.warning("TELEGRAM_BOT_TOKEN not set — skipping bot startup")
            return
        self._bot = Bot(
            token=token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        self._dp = Dispatcher()
        self._dp.include_router(router)
        self._task = asyncio.create_task(self._dp.start_polling(self._bot))
        logger.info("Telegram bot polling started")

    async def stop(self) -> None:
        if self._dp is not None:
            try:
                await self._dp.stop_polling()
            except Exception as exc:  # noqa: BLE001
                logger.debug("dp.stop_polling raised: %s", exc)
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        if self._bot is not None:
            await self._bot.session.close()


bot_runtime = BotRuntime()
