import asyncio
import logging
import re
import os
from datetime import datetime, timedelta, date
from typing import Optional

from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pydantic_core.core_schema import none_schema

from database import Database

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "8762047198:AAFCwDSQTJ0TmX1QfscUIa9CVl3tkSoM-vA")
PROXY = None

session = AiohttpSession(proxy=PROXY) if PROXY else AiohttpSession()
bot = Bot(token=BOT_TOKEN, session=session)
dp = Dispatcher(storage=MemoryStorage())
db = Database("focus_bot.db")
scheduler = AsyncIOScheduler()


class GoalSetup(StatesGroup):
    waiting_for_custom_goal = State()
    waiting_for_custom_duration_value = State()
    waiting_for_custom_duration_unit = State()


class AddTask(StatesGroup):
    waiting_for_task = State()


def format_streak(streak: int) -> str:
    if streak == 0:
        return "🔥 Стрик: 0 дней — начни сегодня!"
    fires = "🔥" * min(streak, 10)
    word = "день" if streak == 1 else "дня" if 2 <= streak <= 4 else "дней"
    return f"{fires} Стрик: {streak} {word}"


def format_progress(streak: int, deadline) -> str:
    if not deadline or streak <= 0:
        return ""
    total_days = (deadline - datetime.now()).days + streak
    if total_days <= 0:
        return ""
    percent = min(int((streak / total_days) * 100), 100)
    filled = int(percent / 10)
    bar = "█" * filled + "░" * (10 - filled)
    return f"[{bar}] {percent}% пройдено"


def hours_to_human(hours: float) -> str:
    if hours < 1:
        return f"{int(hours * 60)} минут"
    if hours < 24:
        h = int(hours)
        return f"{h} {'час' if h == 1 else 'часа' if 2 <= h <= 4 else 'часов'}"
    days = hours / 24
    if days < 7:
        d = int(days)
        return f"{d} {'день' if d == 1 else 'дня' if 2 <= d <= 4 else 'дней'}"
    if days < 30:
        w = int(days / 7)
        return f"{w} {'неделю' if w == 1 else 'недели' if 2 <= w <= 4 else 'недель'}"
    if days < 365:
        m = int(days / 30)
        return f"{m} {'месяц' if m == 1 else 'месяца' if 2 <= m <= 4 else 'месяцев'}"
    y = int(days / 365)
    return f"{y} {'год' if y == 1 else 'года' if 2 <= y <= 4 else 'лет'}"


def deadline_remaining(deadline: datetime) -> str:
    now = datetime.now()
    if deadline <= now:
        return "⏰ Дедлайн истёк!"
    delta = deadline - now
    days = delta.days
    hours = delta.seconds // 3600
    if days > 0:
        return f"⏳ Осталось: {days} дн. {hours} ч."
    return f"⏳ Осталось: {hours} ч. {(delta.seconds % 3600) // 60} мин."


PRESET_GOALS = [
    "Заниматься спортом",
    "Читать каждый день",
    "Учить английский",
]


def kb_cancel():
    b = InlineKeyboardBuilder()
    b.button(text="❌ Отмена", callback_data="cancel")
    return b.as_markup()


def kb_goal_presets_start():
    """Для новых пользователей — без кнопки Отмена"""
    b = InlineKeyboardBuilder()
    for g in PRESET_GOALS:
        b.button(text=g, callback_data=f"goal_preset:{g}")
    b.button(text="✏️ Своя цель", callback_data="goal_custom_start")
    b.adjust(2)
    return b.as_markup()


def kb_goal_presets():
    """Для существующих пользователей — с кнопкой Отмена"""
    b = InlineKeyboardBuilder()
    for g in PRESET_GOALS:
        b.button(text=g, callback_data=f"goal_preset:{g}")
    b.button(text="✏️ Своя цель", callback_data="goal_custom")
    b.button(text="❌ Отмена", callback_data="cancel")
    b.adjust(2)
    return b.as_markup()


def kb_duration_presets():
    b = InlineKeyboardBuilder()
    durations = [
        ("7 дней", 7 * 24),
        ("2 недели", 14 * 24),
        ("1 месяц", 30 * 24),
        ("3 месяца", 90 * 24),
        ("6 месяцев", 180 * 24),
        ("1 год", 365 * 24),
    ]
    for label, hours in durations:
        b.button(text=label, callback_data=f"dur:{hours}")
    b.button(text="⌨️ Своё время", callback_data="dur_custom")
    b.button(text="◀️ Назад", callback_data="back_to_goals")
    b.button(text="❌ Отмена", callback_data="cancel")
    b.adjust(2)
    return b.as_markup()


def kb_duration_units():
    b = InlineKeyboardBuilder()
    units = [("минут", "min"), ("часов", "hour"), ("дней", "day"),
             ("недель", "week"), ("месяцев", "month"), ("лет", "year")]
    for label, code in units:
        b.button(text=label, callback_data=f"unit:{code}")
    b.button(text="❌ Отмена", callback_data="cancel")
    b.adjust(3)
    return b.as_markup()


def kb_notifications():
    b = InlineKeyboardBuilder()
    options = [
        ("1 раз в день", 1), ("2 раза в день", 2),
        ("3 раза в день", 3), ("Каждые 4 часа", 6),
        ("Каждые 2 часа", 12), ("Каждый час", 24),
    ]
    for label, n in options:
        b.button(text=label, callback_data=f"notif:{n}")
    b.button(text="◀️ Назад", callback_data="back_to_duration")
    b.button(text="❌ Отмена", callback_data="cancel")
    b.adjust(2)
    return b.as_markup()


def kb_main_menu():
    b = InlineKeyboardBuilder()
    b.button(text="📊 Мой прогресс", callback_data="menu_status")
    b.button(text="➕ Добавить задачу", callback_data="menu_addtask")
    b.button(text="🔄 Новая цель", callback_data="menu_newgoal")
    b.button(text="✅ Выполнил (уведомлений не будет)", callback_data="done_RN")
    b.button(text="🗑 Удалить мои данные", callback_data="confirm_delete")
    b.adjust(2)
    return b.as_markup()


def kb_after_complete():
    b = InlineKeyboardBuilder()
    b.button(text="📊 Мой прогресс", callback_data="menu_status")
    b.button(text="➕ Добавить задачу", callback_data="menu_addtask")
    b.button(text="🔄 Новая цель", callback_data="menu_newgoal")
    b.button(text="❌ Отмена", callback_data="cancel")
    b.adjust(2)
    return b.as_markup()


def kb_progress_check():
    b = InlineKeyboardBuilder()
    b.button(text="✅ Сделал!", callback_data="check_done")
    b.button(text="⏳ Сделаю позже", callback_data="check_later")
    b.button(text="❌ Сегодня не буду", callback_data="check_skip")
    b.adjust(1)
    return b.as_markup()


def kb_confirm_delete():
    b = InlineKeyboardBuilder()
    b.button(text="Да, удалить всё", callback_data="delete_me")
    b.button(text="❌ Отмена", callback_data="cancel")
    b.adjust(2)
    return b.as_markup()


def kb_my_progress():
    b = InlineKeyboardBuilder()
    b.button(text="➕ Добавить задачу", callback_data="menu_addtask")
    b.button(text="🔄 Новая цель", callback_data="menu_newgoal")
    b.button(text="❌ Отмена", callback_data="cancel")
    b.adjust(2)
    return b.as_markup()


# ─── Cancel / Back ─────────────────────────────────────────────────────────────

async def show_main_menu(target, user_id: int, state: FSMContext = None):
    if state:
        await state.clear()
    user = db.get_user_data(user_id)
    if user and user.get("goal"):
        text = (
            f"Главное меню\n\n"
            f"🎯 Цель: <b>{user['goal']}</b>\n"
            f"{format_streak(user.get('streak', 0))}\n"
            f"{format_progress(user.get('streak', 0), user.get('deadline'))}\n\n"
            f"Что хочешь сделать?"
        )
        markup = kb_main_menu()
    else:
        # Новый пользователь — показываем стартовое меню БЕЗ кнопки Отмена
        text = "🎯 <b>Выбери цель или напиши свою:</b>"
        markup = kb_goal_presets_start()

    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    else:
        await target.answer(text, reply_markup=markup, parse_mode="HTML")


@dp.callback_query(F.data == "cancel")
async def cancel_handler(callback: CallbackQuery, state: FSMContext):
    await show_main_menu(callback, callback.from_user.id, state)


@dp.callback_query(F.data == "back_to_goals")
async def back_to_goals(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    user = db.get_user_data(callback.from_user.id)
    # Если у пользователя уже есть цель — показываем меню с Отменой
    if user and user.get("goal"):
        markup = kb_goal_presets()
    else:
        markup = kb_goal_presets_start()
    await callback.message.edit_text(
        "🎯 <b>Выбери цель или напиши свою:</b>",
        reply_markup=markup,
        parse_mode="HTML"
    )


@dp.callback_query(F.data == "back_to_duration")
async def back_to_duration(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    goal = data.get("goal", "")
    await callback.message.edit_text(
        f"🎯 Цель: <b>{goal}</b>\n\nНа какой срок?",
        reply_markup=kb_duration_presets(),
        parse_mode="HTML"
    )


# ─── /start ────────────────────────────────────────────────────────────────────

@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    db.ensure_user(message.from_user.id)
    user = db.get_user_data(message.from_user.id)
    if user and user.get("goal"):
        await message.answer(
            f"С возвращением!\n\n"
            f"🎯 Цель: <b>{user['goal']}</b>\n"
            f"{format_streak(user.get('streak', 0))}\n"
            f"{format_progress(user.get('streak', 0), user.get('deadline'))}\n\n"
            f"Что хочешь сделать?",
            reply_markup=kb_main_menu(),
            parse_mode="HTML"
        )
    else:
        await message.answer(
            "👋 Привет! Я помогу тебе достигать целей.\n\n"
            "🎯 <b>Выбери цель или напиши свою:</b>",
            reply_markup=kb_goal_presets_start(),
            parse_mode="HTML"
        )


# ─── Выбор цели ────────────────────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("goal_preset:"))
async def goal_preset_chosen(callback: CallbackQuery, state: FSMContext):
    goal = callback.data.split(":", 1)[1]
    await state.update_data(goal=goal)
    await callback.message.edit_text(
        f"🎯 Цель: <b>{goal}</b>\n\nНа какой срок?",
        reply_markup=kb_duration_presets(),
        parse_mode="HTML"
    )


@dp.callback_query(F.data == "goal_custom_start")
async def goal_custom_start(callback: CallbackQuery, state: FSMContext):
    """Своя цель для нового пользователя — кнопка Отмена возвращает на стартовое меню"""
    await callback.message.edit_text("✏️ Напиши свою цель:", reply_markup=kb_cancel())
    await state.set_state(GoalSetup.waiting_for_custom_goal)


@dp.callback_query(F.data == "goal_custom")
async def goal_custom(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("✏️ Напиши свою цель:", reply_markup=kb_cancel())
    await state.set_state(GoalSetup.waiting_for_custom_goal)


@dp.message(GoalSetup.waiting_for_custom_goal)
async def process_custom_goal(message: Message, state: FSMContext):
    goal = message.text.strip()
    await state.update_data(goal=goal)
    await message.answer(
        f"🎯 Цель: <b>{goal}</b>\n\nНа какой срок?",
        reply_markup=kb_duration_presets(),
        parse_mode="HTML"
    )


# ─── Выбор срока ───────────────────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("dur:"))
async def duration_preset_chosen(callback: CallbackQuery, state: FSMContext):
    hours = float(callback.data.split(":")[1])
    human = hours_to_human(hours)
    await state.update_data(duration_hours=hours, duration_human=human)
    await callback.message.edit_text(
        f"⏳ Срок: <b>{human}</b>\n\nКак часто присылать напоминания?",
        reply_markup=kb_notifications(),
        parse_mode="HTML"
    )


@dp.callback_query(F.data == "dur_custom")
async def duration_custom(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "⌨️ Введи число (например: <code>45</code> или <code>3</code>):",
        reply_markup=kb_cancel(),
        parse_mode="HTML"
    )
    await state.set_state(GoalSetup.waiting_for_custom_duration_value)


@dp.message(GoalSetup.waiting_for_custom_duration_value)
async def process_custom_duration_value(message: Message, state: FSMContext):
    text = message.text.strip().replace(",", ".")
    if not re.match(r"^\d+(\.\d+)?$", text):
        await message.answer(
            "❌ Введи просто число, например: <code>30</code>",
            reply_markup=kb_cancel(),
            parse_mode="HTML"
        )
        return
    await state.update_data(custom_value=float(text))
    await message.answer("📏 Теперь выбери единицу времени:", reply_markup=kb_duration_units())
    await state.set_state(GoalSetup.waiting_for_custom_duration_unit)


UNIT_TO_HOURS = {
    "min": 1/60, "hour": 1, "day": 24,
    "week": 24*7, "month": 24*30, "year": 24*365
}


@dp.callback_query(GoalSetup.waiting_for_custom_duration_unit, F.data.startswith("unit:"))
async def process_custom_duration_unit(callback: CallbackQuery, state: FSMContext):
    unit = callback.data.split(":")[1]
    data = await state.get_data()
    hours = data["custom_value"] * UNIT_TO_HOURS[unit]
    human = hours_to_human(hours)
    await state.update_data(duration_hours=hours, duration_human=human)
    await callback.message.edit_text(
        f"⏳ Срок: <b>{human}</b>\n\nКак часто присылать напоминания?",
        reply_markup=kb_notifications(),
        parse_mode="HTML"
    )


# ─── Выбор уведомлений ─────────────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("notif:"))
async def notifications_chosen(callback: CallbackQuery, state: FSMContext):
    notifs_per_day = int(callback.data.split(":")[1])
    data = await state.get_data()
    goal = data.get("goal")
    duration_hours = data.get("duration_hours")
    duration_human = data.get("duration_human")

    if not goal or not duration_hours:
        await callback.answer("Что-то пошло не так, начни заново /start", show_alert=True)
        return

    user_id = callback.from_user.id
    deadline = datetime.now() + timedelta(hours=duration_hours)
    db.save_goal(user_id, goal, deadline, notifs_per_day)
    schedule_notifications(user_id, notifs_per_day)
    await state.clear()

    notif_labels = {1: "1 раз/день", 2: "2 раза/день", 3: "3 раза/день",
                    6: "каждые 4 ч", 12: "каждые 2 ч", 24: "каждый час"}

    await callback.message.edit_text(
        f"🚀 Всё настроено!\n\n"
        f"🎯 Цель: <b>{goal}</b>\n"
        f"⏳ Срок: <b>{duration_human}</b>\n"
        f"📅 Дедлайн: <b>{deadline.strftime('%d.%m.%Y %H:%M')}</b>\n"
        f"🔔 Напоминания: <b>{notif_labels.get(notifs_per_day, str(notifs_per_day))}</b>\n\n"
        f"{format_streak(0)}\n\nУдачи! 💪",
        reply_markup=kb_after_complete(),
        parse_mode="HTML"
    )


# ─── Главное меню ───────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "menu_status")
async def menu_status(callback: CallbackQuery):
    user = db.get_user_data(callback.from_user.id)
    if not user or not user.get("goal"):
        await callback.answer("Нет активной цели!", show_alert=True)
        return

    tasks = db.get_today_tasks(callback.from_user.id)
    tasks_text = ""
    if tasks:
        tasks_text = "\n\nЗадачи на сегодня:\n" + "\n".join(
            f"{'✅' if t['done'] else '⬜'} {t['text']}" for t in tasks
        )

    deadline = user.get("deadline")
    deadline_str = deadline.strftime("%d.%m.%Y %H:%M") if deadline else "—"
    remaining = deadline_remaining(deadline) if deadline else ""

    await callback.message.edit_text(
        f"📊 <b>Твой прогресс</b>\n\n"
        f"🎯 Цель: <b>{user['goal']}</b>\n"
        f"📅 Дедлайн: {deadline_str}\n"
        f"{remaining}\n"
        f"{format_streak(user.get('streak', 0))}\n"
        f"{format_progress(user.get('streak', 0), user.get('deadline'))}"
        f"{tasks_text}",
        reply_markup=kb_my_progress(),
        parse_mode="HTML"
    )


@dp.callback_query(F.data == "menu_newgoal")
async def menu_newgoal(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "🎯 <b>Выбери новую цель:</b>",
        reply_markup=kb_goal_presets(),
        parse_mode="HTML"
    )


@dp.callback_query(F.data == "menu_addtask")
async def menu_addtask(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("➕ Напиши задачу на сегодня:", reply_markup=kb_cancel())
    await state.set_state(AddTask.waiting_for_task)


@dp.message(AddTask.waiting_for_task)
async def process_addtask(message: Message, state: FSMContext):
    task_text = message.text.strip()
    db.add_today_task(message.from_user.id, task_text)
    await state.clear()
    await message.answer(
        f"✅ Задача добавлена: <b>{task_text}</b>\n\nЧто дальше?",
        reply_markup=kb_main_menu(),
        parse_mode="HTML"
    )


# ─── Команды ───────────────────────────────────────────────────────────────────

@dp.message(Command("status"))
async def cmd_status(message: Message):
    user = db.get_user_data(message.from_user.id)
    if not user or not user.get("goal"):
        await message.answer("У тебя нет активной цели.", reply_markup=kb_goal_presets_start())
        return

    tasks = db.get_today_tasks(message.from_user.id)
    tasks_text = ""
    if tasks:
        tasks_text = "\n\nЗадачи на сегодня:\n" + "\n".join(
            f"{'✅' if t['done'] else '⬜'} {t['text']}" for t in tasks
        )

    deadline = user.get("deadline")
    deadline_str = deadline.strftime("%d.%m.%Y %H:%M") if deadline else "—"
    remaining = deadline_remaining(deadline) if deadline else ""

    await message.answer(
        f"📊 <b>Твой прогресс</b>\n\n"
        f"🎯 Цель: <b>{user['goal']}</b>\n"
        f"📅 Дедлайн: {deadline_str}\n"
        f"{remaining}\n"
        f"{format_streak(user.get('streak', 0))}\n"
        f"{format_progress(user.get('streak', 0), user.get('deadline'))}"
        f"{tasks_text}",
        reply_markup=kb_main_menu(),
        parse_mode="HTML"
    )


@dp.message(Command("addtask"))
async def cmd_addtask(message: Message, state: FSMContext):
    user = db.get_user_data(message.from_user.id)
    if not user or not user.get("goal"):
        await message.answer("Сначала настрой цель через /start")
        return
    await message.answer("➕ Напиши задачу на сегодня:", reply_markup=kb_cancel())
    await state.set_state(AddTask.waiting_for_task)


# ─── Выполнил / Прогресс ───────────────────────────────────────────────────────

@dp.callback_query(F.data == "done_RN")
async def done_rn(callback: CallbackQuery):
    user_id = callback.from_user.id
    user = db.get_user_data(user_id)

    if user.get("last_done") == date.today().isoformat():
        await callback.answer("Ты уже отметил выполнение сегодня!", show_alert=True)
        return

    db.mark_today_done(user_id)
    new_streak = db.get_user_data(user_id).get("streak", 0)
    user = db.get_user_data(user_id)
    if user.get("notifs_per_day"):
        pause_until_tomorrow(user_id, user["notifs_per_day"])
    await callback.message.edit_text(
        f"Отлично! Так держать!\n\n"
        f"{format_streak(new_streak)}\n"
        f"{format_progress(new_streak, user.get('deadline'))}\n\n"
        f"Следующее напоминание придёт завтра.",
        reply_markup=kb_after_complete(),
        parse_mode="HTML"
    )


# ─── Уведомления ───────────────────────────────────────────────────────────────

async def send_check_notification(user_id: int):
    user = db.get_user_data(user_id)
    if not user or not user.get("goal"):
        return

    tasks = db.get_today_tasks(user_id)
    tasks_text = ""
    if tasks:
        done = [t for t in tasks if t["done"]]
        pending = [t for t in tasks if not t["done"]]
        if done:
            tasks_text += "\n✅ " + ", ".join(t["text"] for t in done)
        if pending:
            tasks_text += "\n⬜ " + ", ".join(t["text"] for t in pending)

    deadline = user.get("deadline")
    remaining = deadline_remaining(deadline) if deadline else ""

    try:
        await bot.send_message(
            user_id,
            f"🔔 <b>Проверка прогресса!</b>\n\n"
            f"🎯 Цель: <b>{user['goal']}</b>\n"
            f"{remaining}\n"
            f"{format_streak(user.get('streak', 0))}\n"
            f"{format_progress(user.get('streak', 0), user.get('deadline'))}"
            f"{tasks_text}\n\n"
            f"<b>Ты сегодня выполнил свою цель?</b>",
            reply_markup=kb_progress_check(),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Failed to send notification to {user_id}: {e}")


@dp.callback_query(F.data == "check_done")
async def check_done(callback: CallbackQuery):
    user_id = callback.from_user.id
    db.mark_today_done(user_id)
    new_streak = db.get_user_data(user_id).get("streak", 0)
    user = db.get_user_data(user_id)
    if user.get("notifs_per_day"):
        pause_until_tomorrow(user_id, user["notifs_per_day"])
    await callback.message.edit_text(
        f"Отлично! Так держать!\n\n"
        f"{format_streak(new_streak)}\n"
        f"{format_progress(new_streak, user.get('deadline'))}\n\n"
        f"Следующее напоминание придёт завтра.",
        reply_markup=kb_main_menu(),
        parse_mode="HTML"
    )


@dp.callback_query(F.data == "check_later")
async def check_later(callback: CallbackQuery):
    user = db.get_user_data(callback.from_user.id)
    streak = user.get("streak", 0)
    await callback.message.edit_text(
        f"Понял, ждём!\n\n"
        f"⚠️ Помни: если не выполнишь цель сегодня — стрик собьётся!\n"
        f"Сейчас: {format_streak(streak)}\n"
        f"{format_progress(streak, user.get('deadline'))}\n\n"
        f"Я верю в тебя! 💪",
        reply_markup=kb_progress_check(),
        parse_mode="HTML"
    )


@dp.callback_query(F.data == "check_skip")
async def check_skip(callback: CallbackQuery):
    user_id = callback.from_user.id
    old_streak = db.get_user_data(user_id).get("streak", 0)
    db.reset_streak(user_id)

    if old_streak > 0:
        word = "день" if old_streak == 1 else "дней"
        msg = (f"Жаль слышать это.\n\n"
               f"Стрик {old_streak} {word} сброшен до нуля.\n\n"
               f"Не сдавайся! Завтра — новый шанс. 💪")
    else:
        msg = "Ничего страшного. Завтра начнём и построим стрик!"

    await callback.message.edit_text(msg, reply_markup=kb_main_menu(), parse_mode="HTML")


# ─── Удаление данных ───────────────────────────────────────────────────────────

@dp.callback_query(F.data == "confirm_delete")
async def confirm_delete(callback: CallbackQuery):
    await callback.message.edit_text(
        "⚠️ Ты уверен? Все данные, стрик и задачи будут удалены безвозвратно.",
        reply_markup=kb_confirm_delete()
    )


@dp.callback_query(F.data == "delete_me")
async def delete_me(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback.from_user.id
    job_id = f"notif_{user_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    db.delete_user(user_id)
    await callback.message.edit_text(
        "Все твои данные удалены. Напиши /start чтобы начать заново."
    )


@dp.message(Command("deleteme"))
async def cmd_delete(message: Message):
    await message.answer(
        "⚠️ Ты уверен? Все данные, стрик и задачи будут удалены безвозвратно.",
        reply_markup=kb_confirm_delete()
    )


# ─── Планировщик ───────────────────────────────────────────────────────────────

def schedule_notifications(user_id: int, notifs_per_day: int):
    job_id = f"notif_{user_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    interval_hours = 24 / notifs_per_day
    scheduler.add_job(
        send_check_notification, "interval",
        hours=interval_hours, args=[user_id],
        id=job_id, replace_existing=True,
        next_run_time=datetime.now() + timedelta(hours=interval_hours)
    )


def pause_until_tomorrow(user_id: int, notifs_per_day: int):
    job_id = f"notif_{user_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    tomorrow_9am = (datetime.now() + timedelta(days=1)).replace(
        hour=9, minute=11, second=0, microsecond=0
    )
    interval_hours = 24 / notifs_per_day
    scheduler.add_job(
        send_check_notification, "interval",
        hours=interval_hours, args=[user_id],
        id=job_id, replace_existing=True,
        next_run_time=tomorrow_9am
    )


async def restore_schedules():
    users = db.get_all_active_users()
    for user in users:
        if user.get("notifs_per_day"):
            schedule_notifications(user["user_id"], user["notifs_per_day"])
    logger.info(f"Restored schedules for {len(users)} users")


async def main():
    db.init()
    scheduler.start()
    await restore_schedules()
    logger.info("Bot started!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())