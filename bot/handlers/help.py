from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
import asyncpg

from bot.utils.localization import get_text

router = Router()

async def get_user_language(user_id: int, db_pool: asyncpg.Pool) -> str:
    """Вспомогательная функция для получения языка пользователя из БД."""
    async with db_pool.acquire() as connection:
        lang = await connection.fetchval("SELECT language_code FROM users WHERE user_id = $1", user_id)
        return lang or 'ru'

@router.message(Command("help"))
async def help_command_handler(message: Message, db_pool: asyncpg.Pool):
    """
    Обработчик команды /help.
    Выводит справочную информацию о боте.
    """
    lang_code = await get_user_language(message.from_user.id, db_pool)
    
    # Получаем текст справки из файла локализации
    help_text = get_text(lang_code, 'help_command_text')
    # Добавим подсказку о смене языка
    help_text += "\n\n/lang — сменить язык интерфейса"
    
    await message.answer(help_text)