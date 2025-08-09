import asyncio
from aiogram import Router, F, Bot
from aiogram.filters import Filter, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest
import asyncpg

from bot.utils.states import BroadcastState
from bot.utils.localization import get_text
from bot import config # Импортируем конфиг

# Фильтр для проверки, является ли пользователь администратором
class IsAdmin(Filter):
    def __init__(self) -> None:
        self.admin_ids = config.ADMINS

    async def __call__(self, message: Message) -> bool:
        return message.from_user.id in self.admin_ids

# Создаем роутер, который будет работать только для админов
router = Router()
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

async def get_admin_keyboard(lang_code: str) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats"))
    builder.row(InlineKeyboardButton(text="📣 Сделать рассылку", callback_data="admin_broadcast"))
    return builder

@router.message(Command("admin"))
async def admin_panel_handler(message: Message):
    """Обработчик команды /admin, показывает панель администратора."""
    lang_code = 'ru' # Админка пока только на одном языке для простоты
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
        "<b>📊 Статистика Бота</b>\n\n"
        f"👥 Всего пользователей: <b>{total_users}</b>\n"
        f"📢 Всего каналов: <b>{total_channels}</b>\n"
        f"⚙️ Всего сценариев: <b>{total_scenarios}</b> (<i>{active_scenarios} активно</i>)"
    )
    await callback.message.edit_text(stats_text)
    await callback.answer()

# Блок для создания рассылки
@router.callback_query(F.data == "admin_broadcast")
async def start_broadcast_handler(callback: CallbackQuery, state: FSMContext):
    """Начинает процесс создания рассылки."""
    await state.set_state(BroadcastState.waiting_for_message)
    await callback.message.edit_text("Отправьте сообщение, которое вы хотите разослать всем пользователям. Оно будет отправлено 'как есть' (с форматированием, фото, видео и т.д.).")
    await callback.answer()

@router.message(BroadcastState.waiting_for_message)
async def broadcast_message_handler(message: Message, state: FSMContext):
    """Получает сообщение для рассылки и просит подтверждения."""
    await state.update_data(message_to_send=message.model_dump())
    await state.set_state(BroadcastState.confirming_message)

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Отправить", callback_data="confirm_broadcast"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_broadcast")
    )
    await message.answer("Вот ваше сообщение. Вы уверены, что хотите отправить его всем пользователям?", reply_markup=builder.as_markup())
    await message.copy_to(chat_id=message.chat.id)

@router.callback_query(F.data == "cancel_broadcast", BroadcastState.confirming_message)
async def cancel_broadcast_handler(callback: CallbackQuery, state: FSMContext):
    """Отменяет рассылку."""
    await state.clear()
    await callback.message.edit_text("Рассылка отменена.")
    await callback.answer()

@router.callback_query(F.data == "confirm_broadcast", BroadcastState.confirming_message)
async def confirm_broadcast_handler(callback: CallbackQuery, state: FSMContext, db_pool: asyncpg.Pool, bot: Bot):
    """Запускает рассылку после подтверждения."""
    data = await state.get_data()
    message_data = data.get('message_to_send')
    await state.clear()

    if not message_data:
        await callback.message.edit_text("Произошла ошибка, сообщение для рассылки не найдено.")
        return

    message_to_send = Message(**message_data)
    await callback.message.edit_text("Начинаю рассылку...")

    async with db_pool.acquire() as conn:
        user_ids = await conn.fetch("SELECT user_id FROM users")

    sent_count = 0
    failed_count = 0
    total_users = len(user_ids)

    for i, record in enumerate(user_ids):
        user_id = record['user_id']
        try:
            await message_to_send.copy_to(chat_id=user_id)
            sent_count += 1
            await asyncio.sleep(0.1) 
        except Exception:
            failed_count += 1
        
        if (i + 1) % 25 == 0:
            try:
                await callback.message.edit_text(f"Идет рассылка...\n\nОтправлено: {sent_count}/{total_users}\nОшибок: {failed_count}")
            except TelegramBadRequest:
                pass

    await callback.message.edit_text(
        f"✅ Рассылка завершена!\n\n"
        f"Всего пользователей: {total_users}\n"
        f"Успешно отправлено: {sent_count}\n"
        f"Не удалось отправить: {failed_count} (пользователи заблокировали бота)"
    )