from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
import asyncpg

from bot.utils.localization import get_text
from bot.keyboards.inline import language_selection_keyboard, get_main_menu_keyboard, get_cancel_add_channel_keyboard
from bot.utils.states import AddChannel

router = Router()

async def get_user_language(user_id: int, db_pool: asyncpg.Pool) -> str:
    """Вспомогательная функция для получения языка пользователя из БД."""
    async with db_pool.acquire() as connection:
        lang = await connection.fetchval("SELECT language_code FROM users WHERE user_id = $1", user_id)
        return lang or 'ru'

async def show_main_menu(message: Message | CallbackQuery, lang_code: str):
    """Отправляет или редактирует сообщение, показывая главное меню."""
    text = get_text(lang_code, 'main_menu_title')
    keyboard = get_main_menu_keyboard(lang_code)
    
    if isinstance(message, CallbackQuery):
        if message.message.text != text:
            await message.message.edit_text(text, reply_markup=keyboard)
    else:
        await message.answer(text, reply_markup=keyboard)

@router.message(CommandStart())
async def command_start_handler(message: Message, db_pool: asyncpg.Pool, state: FSMContext):
    """
    Обработчик команды /start.
    Если пользователь уже есть, показывает главное меню.
    Если пользователь новый, показывает выбор языка.
    """
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", message.from_user.id)
    
    if user:
        # Пользователь уже зарегистрирован, показываем главное меню
        await state.clear() # Сбрасываем любой предыдущий стейт
        lang_code = user['language_code']
        await show_main_menu(message, lang_code)
    else:
        # Новый пользователь, начинаем с выбора языка
        await message.answer(get_text("ru", "choose_language"), reply_markup=language_selection_keyboard())

@router.callback_query(F.data.startswith("lang_"))
async def language_selection_callback(callback: CallbackQuery, db_pool: asyncpg.Pool, state: FSMContext):
    """
    Обрабатывает выбор языка, сохраняет его и ЗАПУСКАЕТ ПРОЦЕСС ОНБОРДИНГА.
    """
    lang_code = callback.data.split("_")[1]
    user_id = callback.from_user.id
    username = callback.from_user.username or ''

    async with db_pool.acquire() as connection:
        await connection.execute(
            "INSERT INTO users (user_id, username, language_code) VALUES ($1, $2, $3) "
            "ON CONFLICT (user_id) DO UPDATE SET username = EXCLUDED.username, language_code = EXCLUDED.language_code;",
            user_id, username, lang_code
        )
    
    # --- НАЧАЛО ОНБОРДИНГА ---
    await state.set_state(AddChannel.waiting_for_input)
    
    keyboard = get_cancel_add_channel_keyboard(lang_code)
    text = get_text(lang_code, 'onboarding_step1_welcome') + "\n\n" + get_text(lang_code, 'add_channel_unified_prompt')
    
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer(get_text(lang_code, "user_added"))

@router.callback_query(F.data == "back_to_main_menu")
async def back_to_main_menu_handler(callback: CallbackQuery, db_pool: asyncpg.Pool, state: FSMContext):
    """Обрабатывает кнопку 'Назад в меню', показывая чистое главное меню и сбрасывая стейт."""
    await state.clear()
    lang_code = await get_user_language(callback.from_user.id, db_pool)
    await show_main_menu(callback, lang_code)
    await callback.answer()