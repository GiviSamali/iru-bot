import asyncio
import sqlite3
import logging
from datetime import datetime
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.session.aiohttp import AiohttpSession

from config import BOT_TOKEN, ADMIN_TELEGRAM_ID, DB_PATH, PROXY_URL

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── Bot with optional SOCKS5 proxy ─────────────────────────────────────────

if PROXY_URL:
    try:
        from aiohttp_socks import ProxyConnector
        _connector = ProxyConnector.from_url(PROXY_URL)
        _session = AiohttpSession(connector=_connector)
        bot = Bot(token=BOT_TOKEN, session=_session)
        logger.info(f"Прокси активен: {PROXY_URL}")
    except ImportError:
        logger.warning("aiohttp-socks не установлен, запуск без прокси")
        bot = Bot(token=BOT_TOKEN)
else:
    bot = Bot(token=BOT_TOKEN)

dp = Dispatcher(storage=MemoryStorage())


# ─── FSM ────────────────────────────────────────────────────────────────────

class ApplyForm(StatesGroup):
    waiting_name = State()
    waiting_reason = State()
    waiting_contact = State()


# ─── DB helpers ─────────────────────────────────────────────────────────────

def db_connect():
    return sqlite3.connect(DB_PATH)


def get_user_by_tg(tg_id: int):
    with db_connect() as conn:
        row = conn.execute(
            "SELECT id, username, plan, is_admin FROM users WHERE telegram_id = ?",
            (tg_id,)
        ).fetchone()
    return row  # (id, username, plan, is_admin) или None


def get_pending_applications():
    with db_connect() as conn:
        rows = conn.execute(
            """SELECT id, tg_id, tg_username, name, reason, contact, created_at
               FROM token_applications WHERE status = 'pending'
               ORDER BY created_at ASC"""
        ).fetchall()
    return rows


def create_application(tg_id, tg_username, name, reason, contact):
    with db_connect() as conn:
        existing = conn.execute(
            "SELECT id FROM token_applications WHERE tg_id = ? AND status = 'pending'",
            (tg_id,)
        ).fetchone()
        if existing:
            return None, "already_exists"

        conn.execute(
            """INSERT INTO token_applications
               (tg_id, tg_username, name, reason, contact, status, created_at)
               VALUES (?, ?, ?, ?, ?, 'pending', ?)""",
            (tg_id, tg_username, name, reason, contact,
             datetime.utcnow().isoformat())
        )
        conn.commit()
        app_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return app_id, "ok"


def approve_application(app_id: int, plan: str = "free"):
    with db_connect() as conn:
        app = conn.execute(
            "SELECT tg_id, tg_username, name FROM token_applications WHERE id = ?",
            (app_id,)
        ).fetchone()
        if not app:
            return None

        tg_id, tg_username, name = app

        existing_user = conn.execute(
            "SELECT id FROM users WHERE telegram_id = ?", (tg_id,)
        ).fetchone()

        if existing_user:
            conn.execute(
                "UPDATE users SET plan = ? WHERE telegram_id = ?",
                (plan, tg_id)
            )
        else:
            conn.execute(
                """INSERT INTO users (username, telegram_id, plan, is_admin, created_at)
                   VALUES (?, ?, ?, 0, ?)""",
                (tg_username or name, tg_id, plan, datetime.utcnow().isoformat())
            )

        conn.execute(
            "UPDATE token_applications SET status = 'approved' WHERE id = ?",
            (app_id,)
        )
        conn.commit()

        user_row = conn.execute(
            "SELECT username FROM users WHERE telegram_id = ?", (tg_id,)
        ).fetchone()

    return {"tg_id": tg_id, "username": user_row[0] if user_row else tg_username}


def reject_application(app_id: int):
    with db_connect() as conn:
        app = conn.execute(
            "SELECT tg_id FROM token_applications WHERE id = ?", (app_id,)
        ).fetchone()
        if not app:
            return None
        conn.execute(
            "UPDATE token_applications SET status = 'rejected' WHERE id = ?",
            (app_id,)
        )
        conn.commit()
    return app[0]


def ensure_applications_table():
    with db_connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS token_applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER NOT NULL,
                tg_username TEXT,
                name TEXT,
                reason TEXT,
                contact TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT
            )
        """)
        try:
            conn.execute("ALTER TABLE users ADD COLUMN telegram_id INTEGER")
        except Exception:
            pass
        conn.commit()


# ─── Keyboards ───────────────────────────────────────────────────────────────

def main_kb(is_registered: bool) -> ReplyKeyboardMarkup:
    buttons = []
    if not is_registered:
        buttons.append([KeyboardButton(text="📋 Подать заявку на токен")])
    else:
        buttons.append([KeyboardButton(text="👤 Мой профиль")])
    buttons.append([KeyboardButton(text="ℹ️ Что такое ИРУ?")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Заявки", callback_data="admin_apps")],
        [InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users")],
    ])


def app_action_kb(app_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Одобрить (free)",
                callback_data=f"approve:{app_id}:free"
            ),
            InlineKeyboardButton(
                text="⭐ Одобрить (pro)",
                callback_data=f"approve:{app_id}:pro"
            ),
        ],
        [
            InlineKeyboardButton(
                text="❌ Отклонить",
                callback_data=f"reject:{app_id}"
            )
        ]
    ])


# ─── Handlers: /start ────────────────────────────────────────────────────────

@dp.message(CommandStart())
async def cmd_start(message: Message):
    tg_id = message.from_user.id
    user = get_user_by_tg(tg_id)
    is_registered = user is not None

    if is_registered:
        plan_label = {"free": "🆓 Free", "pro": "⭐ Pro", "business": "🏢 Business"}.get(
            user[2], user[2]
        )
        text = (
            f"👋 С возвращением, <b>{user[1]}</b>!\n\n"
            f"Твой план: {plan_label}\n"
            f"Статус: {'👑 Администратор' if user[3] else '👤 Пользователь'}"
        )
    else:
        text = (
            "👋 Добро пожаловать в <b>ИРУ</b> — систему управления ПК голосом и текстом!\n\n"
            "Для использования системы нужен токен доступа.\n"
            "Нажми кнопку ниже, чтобы подать заявку."
        )

    await message.answer(text, parse_mode="HTML",
                         reply_markup=main_kb(is_registered))

    if tg_id == ADMIN_TELEGRAM_ID:
        await message.answer("🔧 <b>Панель администратора</b>",
                             parse_mode="HTML", reply_markup=admin_kb())


# ─── Handlers: Что такое ИРУ ─────────────────────────────────────────────────

@dp.message(F.text == "ℹ️ Что такое ИРУ?")
async def about_iru(message: Message):
    await message.answer(
        "🤖 <b>ИРУ — Интеллектуальный Режим Управления</b>\n\n"
        "Система управления компьютером с помощью:\n"
        "• 🗣 Голосовых команд\n"
        "• 💬 Текстовых запросов на естественном языке\n"
        "• 🔧 DEV-режима для прямых команд\n\n"
        "Сервер обрабатывает команду через LLM и выполняет её в PowerShell/CMD.\n\n"
        "🔗 GitHub: github.com/GiviSamali/IRU",
        parse_mode="HTML"
    )


# ─── Handlers: Мой профиль ───────────────────────────────────────────────────

@dp.message(F.text == "👤 Мой профиль")
async def my_profile(message: Message):
    tg_id = message.from_user.id
    user = get_user_by_tg(tg_id)
    if not user:
        await message.answer("❗ Ты не зарегистрирован. Подай заявку на токен.")
        return

    plan_label = {"free": "🆓 Free", "pro": "⭐ Pro", "business": "🏢 Business"}.get(
        user[2], user[2]
    )
    await message.answer(
        f"👤 <b>Профиль</b>\n\n"
        f"Имя пользователя: <code>{user[1]}</code>\n"
        f"План: {plan_label}\n"
        f"Роль: {'👑 Администратор' if user[3] else '👤 Пользователь'}\n\n"
        f"Токен доступа выдаётся при входе в приложение ИРУ.",
        parse_mode="HTML"
    )


# ─── Handlers: Подать заявку (FSM) ───────────────────────────────────────────

@dp.message(F.text == "📋 Подать заявку на токен")
async def apply_start(message: Message, state: FSMContext):
    tg_id = message.from_user.id
    user = get_user_by_tg(tg_id)
    if user:
        await message.answer("✅ У тебя уже есть доступ к ИРУ!")
        return

    with db_connect() as conn:
        existing = conn.execute(
            "SELECT status FROM token_applications WHERE tg_id = ? ORDER BY created_at DESC LIMIT 1",
            (tg_id,)
        ).fetchone()

    if existing:
        if existing[0] == "pending":
            await message.answer("⏳ У тебя уже есть активная заявка. Ожидай ответа от администратора.")
            return
        elif existing[0] == "rejected":
            await message.answer("Твоя предыдущая заявка была отклонена. Можешь подать новую.")

    await message.answer(
        "📋 <b>Заявка на доступ к ИРУ</b>\n\nШаг 1/3: Как тебя зовут?",
        parse_mode="HTML"
    )
    await state.set_state(ApplyForm.waiting_name)


@dp.message(ApplyForm.waiting_name)
async def apply_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await message.answer(
        "Шаг 2/3: Зачем тебе нужен доступ к ИРУ?\n"
        "<i>(опиши коротко свой use-case)</i>",
        parse_mode="HTML"
    )
    await state.set_state(ApplyForm.waiting_reason)


@dp.message(ApplyForm.waiting_reason)
async def apply_reason(message: Message, state: FSMContext):
    await state.update_data(reason=message.text.strip())
    await message.answer(
        "Шаг 3/3: Укажи контакт для связи\n"
        "<i>(email, ник в другом мессенджере или просто напиши «Telegram»)</i>",
        parse_mode="HTML"
    )
    await state.set_state(ApplyForm.waiting_contact)


@dp.message(ApplyForm.waiting_contact)
async def apply_contact(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()

    tg_id = message.from_user.id
    tg_username = message.from_user.username or ""
    name = data["name"]
    reason = data["reason"]
    contact = message.text.strip()

    app_id, status = create_application(tg_id, tg_username, name, reason, contact)

    if status == "already_exists":
        await message.answer("⏳ У тебя уже есть активная заявка. Ожидай ответа.")
        return

    await message.answer(
        f"✅ <b>Заявка #{app_id} отправлена!</b>\n\n"
        f"Администратор рассмотрит её и уведомит тебя здесь.\n"
        f"Обычно это занимает несколько часов.",
        parse_mode="HTML"
    )

    if ADMIN_TELEGRAM_ID:
        await bot.send_message(
            ADMIN_TELEGRAM_ID,
            f"🔔 <b>Новая заявка #{app_id}</b>\n\n"
            f"👤 {name} (@{tg_username or 'нет username'})\n"
            f"📝 Причина: {reason}\n"
            f"📞 Контакт: {contact}\n"
            f"🆔 TG ID: <code>{tg_id}</code>",
            parse_mode="HTML",
            reply_markup=app_action_kb(app_id)
        )


# ─── Handlers: Admin callbacks ───────────────────────────────────────────────

@dp.callback_query(F.data == "admin_apps")
async def admin_show_apps(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_TELEGRAM_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return

    apps = get_pending_applications()
    if not apps:
        await callback.message.answer("✅ Нет новых заявок.")
        await callback.answer()
        return

    for app in apps:
        app_id, tg_id, tg_username, name, reason, contact, created_at = app
        await callback.message.answer(
            f"📋 <b>Заявка #{app_id}</b>\n\n"
            f"👤 {name} (@{tg_username or '—'})\n"
            f"📝 {reason}\n"
            f"📞 {contact}\n"
            f"🆔 <code>{tg_id}</code>\n"
            f"🕐 {created_at[:16]}",
            parse_mode="HTML",
            reply_markup=app_action_kb(app_id)
        )
    await callback.answer()


@dp.callback_query(F.data.startswith("approve:"))
async def admin_approve(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_TELEGRAM_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return

    _, app_id_str, plan = callback.data.split(":")
    app_id = int(app_id_str)

    result = approve_application(app_id, plan)
    if not result:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        f"✅ Заявка #{app_id} одобрена. Пользователь <b>{result['username']}</b> "
        f"получил план <b>{plan}</b>.",
        parse_mode="HTML"
    )

    try:
        await bot.send_message(
            result["tg_id"],
            f"🎉 <b>Заявка одобрена!</b>\n\n"
            f"Твой аккаунт активирован с планом <b>{plan}</b>.\n\n"
            f"Теперь можешь скачать агент ИРУ и войти со своим логином.",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.warning(f"Не удалось уведомить пользователя {result['tg_id']}: {e}")

    await callback.answer("Одобрено!")


@dp.callback_query(F.data.startswith("reject:"))
async def admin_reject(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_TELEGRAM_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return

    app_id = int(callback.data.split(":")[1])
    tg_id = reject_application(app_id)

    if not tg_id:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(f"❌ Заявка #{app_id} отклонена.")

    try:
        await bot.send_message(
            tg_id,
            "❌ <b>Заявка отклонена.</b>\n\n"
            "К сожалению, администратор отклонил твою заявку.\n"
            "Ты можешь подать новую заявку позже.",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.warning(f"Не удалось уведомить пользователя {tg_id}: {e}")

    await callback.answer("Отклонено.")


# ─── /admin команда ──────────────────────────────────────────────────────────

@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id != ADMIN_TELEGRAM_ID:
        await message.answer("🚫 Нет доступа.")
        return
    await message.answer("🔧 <b>Панель администратора</b>",
                         parse_mode="HTML", reply_markup=admin_kb())


# ─── Entry point ─────────────────────────────────────────────────────────────

async def main():
    ensure_applications_table()
    logger.info("ИРУ-бот запущен")
    await dp.start_polling(bot, skip_updates=True)


if __name__ == "__main__":
    asyncio.run(main())
