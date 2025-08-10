# -*- coding: utf-8 -*-
import asyncio
import logging
from aiogram import Router, F, Bot, types
from aiogram.filters import Filter, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest
import asyncpg
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.utils.states import BroadcastState, DirectMessage, PromoCodeCreation
from bot.utils.localization import get_text
from bot import config

# Фильтр для проверки, является ли пользователь администратором
class IsAdmin(Filter):
    def __init__(self) -> None:
        self.admin_ids = config.ADMINS

    async def __call__(self, message: Message) -> bool:
        return message.from_user.id in self.admin_ids

async def ensure_user_in_db(user: types.User, db_pool: asyncpg.Pool):
    """Проверяет, есть ли пользователь в таблице users, и если нет - добавляет."""
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users (user_id, username, language_code) 
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id) DO NOTHING
            """,
            user.id,
            user.username or '',
            user.language_code or 'ru'
        )

# Создаем роутер, который будет работать только для админов
router = Router()
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


async def get_admin_keyboard(lang_code: str) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats"))
    builder.row(
        InlineKeyboardButton(text=get_text(lang_code, 'admin_broadcast_button'), callback_data="admin_broadcast"),
        InlineKeyboardButton(text=get_text(lang_code, 'admin_write_to_user_button'), callback_data="admin_direct_message")
    )
    builder.row(InlineKeyboardButton(text="🎁 Промокоды", callback_data="admin_promo_menu"))
    return builder

@router.message(Command("admin"))
async def admin_panel_handler(message: Message, db_pool: asyncpg.Pool):
    """Обработчик команды /admin, показывает панель администратора."""
    await ensure_user_in_db(message.from_user, db_pool)
    lang_code = 'ru'
    keyboard = await get_admin_keyboard(lang_code)
    await message.answer("Добро пожаловать в панель администратора!", reply_markup=keyboard.as_markup())

@router.callback_query(F.data == "admin_stats")
async def admin_stats_handler(callback: CallbackQuery, db_pool: asyncpg.Pool):
    """Показывает статистику по боту."""
    async with db_pool.acquire() as conn:
        total_users = await conn.fetchval("SELECT COUNT(*) FROM users")
        total_channels = await conn.fetchval("SELECT COUNT(*) FROM channels")
        total_scenarios = await conn.fetchval("SELECT COUNT(*) FROM posting_scenarios")
        active_scenarios = await conn.fetchval("SELECT COUNT(*) FROM posting_scenarios WHERE is_active = TRUE")
        
    stats_text = (
        f"<b>📊 Статистика Бота</b>\n\n"
        f"👥 Всего пользователей: <b>{total_users}</b>\n"
        f"📢 Всего каналов: <b>{total_channels}</b>\n"
        f"⚙️ Всего сценариев: <b>{total_scenarios}</b> (<i>{active_scenarios} активно</i>)"
    )
    await callback.message.edit_text(stats_text)
    await callback.answer()

# --- БЛОК РАССЫЛКИ ---

async def _send_broadcast_message(bot: Bot, user_id: int, from_chat_id: int, message_id: int) -> bool:
    try:
        await bot.copy_message(chat_id=user_id, from_chat_id=from_chat_id, message_id=message_id)
        return True
    except Exception:
        return False

@router.callback_query(F.data == "admin_broadcast")
async def start_broadcast_handler(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BroadcastState.waiting_for_message)
    await callback.message.edit_text("Отправьте сообщение для рассылки. Оно будет скопировано всем пользователям.")
    await callback.answer()

@router.message(BroadcastState.waiting_for_message)
async def broadcast_message_handler(message: Message, state: FSMContext):
    await state.update_data(message_id=message.message_id, from_chat_id=message.chat.id)
    await state.set_state(BroadcastState.confirming_message)
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Отправить", callback_data="confirm_broadcast"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_broadcast")
    )
    await message.answer("Вы уверены, что хотите разослать это сообщение всем пользователям?", reply_markup=builder.as_markup())

@router.callback_query(F.data == "confirm_broadcast", BroadcastState.confirming_message)
async def confirm_broadcast_handler(callback: CallbackQuery, state: FSMContext, db_pool: asyncpg.Pool, bot: Bot):
    data = await state.get_data()
    message_id = data.get('message_id')
    from_chat_id = data.get('from_chat_id')
    await state.clear()

    if not message_id or not from_chat_id:
        await callback.message.edit_text("Ошибка: сообщение для рассылки не найдено. Попробуйте снова.")
        return

    await callback.message.edit_text("Начинаю рассылку...")
    async with db_pool.acquire() as conn:
        user_ids = await conn.fetch("SELECT user_id FROM users")

    sent_count = 0
    failed_count = 0
    total_users = len(user_ids)
    for i, record in enumerate(user_ids):
        if await _send_broadcast_message(bot, record['user_id'], from_chat_id, message_id):
            sent_count += 1
        else:
            failed_count += 1
        await asyncio.sleep(0.1)
        if (i + 1) % 25 == 0:
            try:
                await callback.message.edit_text(f"Идет рассылка...\n\nОтправлено: {sent_count}/{total_users}\nОшибок: {failed_count}")
            except TelegramBadRequest: pass

    await callback.message.edit_text(
        f"✅ Рассылка завершена!\n\n"
        f"Всего пользователей: {total_users}\n"
        f"Успешно отправлено: {sent_count}\n"
        f"Не удалось отправить: {failed_count} (пользователи заблокировали бота)"
    )

@router.callback_query(F.data == "cancel_broadcast", BroadcastState.confirming_message)
async def cancel_broadcast_handler(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Рассылка отменена.")
    await callback.answer()


# --- БЛОК: ПРЯМОЕ СООБЩЕНИЕ ПОЛЬЗОВАТЕЛЮ ---

@router.callback_query(F.data == "admin_direct_message")
async def start_direct_message_handler(callback: CallbackQuery, state: FSMContext):
    await state.set_state(DirectMessage.waiting_for_user_id)
    await callback.message.edit_text("Отправьте ID или юзернейм (@username) пользователя, которому хотите написать.")
    await callback.answer()

@router.message(DirectMessage.waiting_for_user_id)
async def process_direct_message_user_id(message: Message, state: FSMContext):
    target_user = message.text.strip()
    await state.update_data(target_user=target_user)
    await state.set_state(DirectMessage.waiting_for_message)
    await message.answer(f"Теперь отправьте сообщение, которое хотите переслать пользователю {target_user}.")

@router.message(DirectMessage.waiting_for_message)
async def process_direct_message_content(message: Message, state: FSMContext):
    data = await state.get_data()
    target_user = data.get('target_user')
    
    await state.update_data(message_id=message.message_id, from_chat_id=message.chat.id)
    await state.set_state(DirectMessage.confirming_message)

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Отправить", callback_data="confirm_direct_message"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_direct_message")
    )
    await message.answer(f"Вы уверены, что хотите отправить это сообщение пользователю {target_user}?", reply_markup=builder.as_markup())

@router.callback_query(F.data == "confirm_direct_message", DirectMessage.confirming_message)
async def confirm_direct_message_handler(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    target_user = data.get('target_user')
    message_id = data.get('message_id')
    from_chat_id = data.get('from_chat_id')
    await state.clear()

    if not all([target_user, message_id, from_chat_id]):
        await callback.message.edit_text("Ошибка: не хватает данных для отправки. Попробуйте снова.")
        return

    try:
        await bot.copy_message(chat_id=target_user, from_chat_id=from_chat_id, message_id=message_id)
        await callback.message.edit_text(f"✅ Сообщение успешно отправлено пользователю {target_user}.")
    except Exception as e:
        await callback.message.edit_text(f"❌ Не удалось отправить сообщение.\nОшибка: {e}")
    await callback.answer()

@router.callback_query(F.data == "cancel_direct_message")
async def cancel_direct_message_handler(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Отправка сообщения отменена.")
    await callback.answer()

# --- БЛОК: УПРАВЛЕНИЕ ПРОМОКОДАМИ ---

@router.callback_query(F.data == "admin_promo_menu")
async def promo_menu_handler(callback: CallbackQuery, db_pool: asyncpg.Pool, bot: Bot):
    async with db_pool.acquire() as conn:
        promo_codes = await conn.fetch("SELECT * FROM promo_codes ORDER BY created_at DESC")
    
    text = "<b>🎁 Управление промокодами</b>\n\n"
    builder = InlineKeyboardBuilder()

    if not promo_codes:
        text += "Промокодов еще не создано."
    else:
        for code in promo_codes:
            status = "✅ Активен" if code['is_active'] else "❌ Неактивен"
            text += f"<code>{code['promo_code']}</code> | +{code['generations_awarded']} gen | {code['uses_left']}/{code['total_uses']} | {status}\n\n"
            
            toggle_text = "❌ Деактивировать" if code['is_active'] else "✅ Активировать"
            builder.row(
                InlineKeyboardButton(text=toggle_text, callback_data=f"promo_toggle_{code['id']}"),
                InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"promo_delete_{code['id']}")
            )
            builder.row(InlineKeyboardButton(text="-"*20, callback_data="noop"))
    
    builder.row(InlineKeyboardButton(text="⊕ Создать новый", callback_data="promo_create_start"))
    builder.row(InlineKeyboardButton(text="⬅️ Назад в админку", callback_data="back_to_admin"))
    
    if callback.message:
        try:
            await callback.message.edit_text(text, reply_markup=builder.as_markup())
        except TelegramBadRequest: # Если сообщение не изменилось
            pass
    else:
        await bot.send_message(callback.from_user.id, text, reply_markup=builder.as_markup())
    
    if callback.message:
        await callback.answer()

@router.callback_query(F.data.startswith("promo_toggle_"))
async def toggle_promo_code_handler(callback: CallbackQuery, db_pool: asyncpg.Pool, bot: Bot):
    promo_id = int(callback.data.split("_")[2])
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE promo_codes SET is_active = NOT is_active WHERE id = $1", promo_id)
    await callback.answer("Статус промокода изменен.")
    await promo_menu_handler(callback, db_pool, bot)

@router.callback_query(F.data.startswith("promo_delete_"))
async def delete_promo_code_handler(callback: CallbackQuery, db_pool: asyncpg.Pool, bot: Bot):
    promo_id = int(callback.data.split("_")[2])
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM promo_codes WHERE id = $1", promo_id)
    await callback.answer("Промокод удален.", show_alert=True)
    await promo_menu_handler(callback, db_pool, bot)

@router.callback_query(F.data == "noop")
async def noop_handler(callback: CallbackQuery):
    await callback.answer()

@router.callback_query(F.data == "back_to_admin")
async def back_to_admin_handler(callback: CallbackQuery):
    keyboard = await get_admin_keyboard('ru')
    await callback.message.edit_text("Добро пожаловать в панель администратора!", reply_markup=keyboard.as_markup())
    await callback.answer()

@router.callback_query(F.data == "promo_create_start")
async def start_promo_creation(callback: CallbackQuery, state: FSMContext):
    await state.set_state(PromoCodeCreation.waiting_for_name)
    await callback.message.edit_text("<b>Шаг 1/3:</b> Введите название для нового промокода (например, `NEWYEAR2025`).")
    await callback.answer()

@router.message(PromoCodeCreation.waiting_for_name)
async def process_promo_name(message: Message, state: FSMContext):
    await state.update_data(promo_name=message.text.strip())
    await state.set_state(PromoCodeCreation.waiting_for_generations)
    await message.answer("<b>Шаг 2/3:</b> Сколько генераций будет давать этот промокод?")

@router.message(PromoCodeCreation.waiting_for_generations)
async def process_promo_generations(message: Message, state: FSMContext):
    if not message.text.isdigit() or int(message.text) <= 0:
        await message.reply("Пожалуйста, введите положительное число.")
        return
    await state.update_data(generations=int(message.text))
    await state.set_state(PromoCodeCreation.waiting_for_uses)
    await message.answer("<b>Шаг 3/3:</b> Сколько раз можно будет использовать этот промокод (общий лимит)?")

@router.message(PromoCodeCreation.waiting_for_uses)
async def process_promo_uses(message: Message, state: FSMContext, db_pool: asyncpg.Pool, bot: Bot):
    if not message.text.isdigit() or int(message.text) <= 0:
        await message.reply("Пожалуйста, введите положительное число.")
        return
    
    data = await state.get_data()
    promo_name = data['promo_name']
    generations = data['generations']
    total_uses = int(message.text)
    
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO promo_codes (promo_code, generations_awarded, total_uses, uses_left, created_by)
                   VALUES ($1, $2, $3, $4, $5)""",
                promo_name, generations, total_uses, total_uses, message.from_user.id
            )
        await message.answer(f"✅ Промокод <code>{promo_name}</code> успешно создан!")
    except asyncpg.UniqueViolationError:
        await message.answer("❌ Ошибка: промокод с таким названием уже существует.")
    except Exception as e:
        await message.answer(f"❌ Произошла непредвиденная ошибка: {e}")
    
    await state.clear()
    
    cb_mock = CallbackQuery(id="mock_promo_menu", from_user=message.from_user, chat_instance="mock", data="admin_promo_menu", message=None)
    await promo_menu_handler(cb_mock, db_pool, bot)


# --- Обработчик для проверки "здоровья" бота ---
@router.message(Command("health"))
async def health_check_handler(message: Message, db_pool: asyncpg.Pool, scheduler: AsyncIOScheduler):
    db_status = "❌ Ошибка"
    db_error = ""
    scheduler_status = "❌ Ошибка"
    
    try:
        async with db_pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        db_status = "✅ OK"
    except Exception as e:
        db_error = str(e)
        logging.error(f"Health Check: DB connection failed: {e}")

    try:
        if scheduler.running:
            scheduler_status = "✅ OK (запущен)"
        else:
            scheduler_status = "⚠️ Внимание (остановлен)"
    except Exception as e:
        scheduler_status = f"❌ Ошибка: {e}"

    health_report = (
        "<b>🩺 Отчет о состоянии бота</b>\n\n"
        f"<b>База данных (PostgreSQL):</b> {db_status}\n"
        f"<b>Планировщик (APScheduler):</b> {scheduler_status}\n"
    )
    if db_error:
        health_report += f"\n<i>Подробности ошибки БД:</i> <pre>{db_error}</pre>"

    await message.answer(health_report)