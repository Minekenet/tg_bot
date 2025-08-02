from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import CommandStart
import asyncpg

from bot.utils.localization import get_text
from bot.keyboards.inline import language_selection_keyboard, get_main_menu_keyboard

router = Router()

async def get_user_language(user_id: int, db_pool: asyncpg.Pool) -> str:
    """Вспомогательная функция для получения языка пользователя из БД."""
    async with db_pool.acquire() as connection:
        lang = await connection.fetchval("SELECT language_code FROM users WHERE user_id = $1", user_id)
        return lang or 'ru'

async def show_main_menu(message: Message | CallbackQuery, lang_code: str):
    """Отправляет или редактирует сообщение, показывая главное меню."""
    # Убираем приветствие, чтобы меню было чистым при возврате
    text = get_text(lang_code, 'main_menu_title')
    keyboard = get_main_menu_keyboard(lang_code)
    
    if isinstance(message, CallbackQuery):
        # Проверяем, чтобы не редактировать одно и то же
        if message.message.text != text:
            await message.message.edit_text(text, reply_markup=keyboard)
    else:
        await message.answer(text, reply_markup=keyboard)

@router.message(CommandStart())
async def command_start_handler(message: Message):
    """Обработчик команды /start. Показывает клавиатуру выбора языка."""
    await message.answer(get_text("ru", "choose_language"), reply_markup=language_selection_keyboard())

@router.callback_query(F.data.startswith("lang_"))
async def language_selection_callback(callback: CallbackQuery, db_pool: asyncpg.Pool):
    """Обрабатывает выбор языка, сохраняет его и показывает главное меню."""
    lang_code = callback.data.split("_")[1]
    user_id = callback.from_user.id
    username = callback.from_user.username or ''

    async with db_pool.acquire() as connection:
        await connection.execute(
            "INSERT INTO users (user_id, username, language_code) VALUES ($1, $2, $3) "
            "ON CONFLICT (user_id) DO UPDATE SET username = EXCLUDED.username, language_code = EXCLUDED.language_code;",
            user_id, username, lang_code
        )
    
    # Показываем приветствие + главное меню в первый раз
    welcome_text = f"{get_text(lang_code, 'welcome_message')}\n\n{get_text(lang_code, 'main_menu_title')}"
    keyboard = get_main_menu_keyboard(lang_code)
    await callback.message.edit_text(welcome_text, reply_markup=keyboard)
    await callback.answer(get_text(lang_code, "user_added"))

# --- ОБРАБОТЧИК ДЛЯ КНОПКИ "НАЗАД В МЕНЮ" ---
@router.callback_query(F.data == "back_to_main_menu")
async def back_to_main_menu_handler(callback: CallbackQuery, db_pool: asyncpg.Pool):
    """Обрабатывает кнопку 'Назад в меню', показывая чистое главное меню."""
    lang_code = await get_user_language(callback.from_user.id, db_pool)
    await show_main_menu(callback, lang_code)
    await callback.answer()

# --- СТАРЫЕ ОБРАБОТЧИКИ УДАЛЕНЫ ---
# Мы полностью удалили отсюда обработчики для кнопок "subscription" и "support",
# так как они теперь находятся в своих собственных файлах (или будут там).