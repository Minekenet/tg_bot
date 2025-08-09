from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
import asyncpg

from bot.utils.localization import get_text
from bot.utils.states import PromoCodeActivation
from bot.keyboards.inline import get_cancel_add_channel_keyboard

router = Router()

async def get_user_language(user_id: int, db_pool: asyncpg.Pool) -> str:
    async with db_pool.acquire() as connection:
        return await connection.fetchval("SELECT language_code FROM users WHERE user_id = $1", user_id) or 'ru'

@router.message(Command("promo"))
async def promo_command_handler(message: Message, state: FSMContext):
    lang_code = await get_user_language(message.from_user.id, state.storage)
    await state.set_state(PromoCodeActivation.waiting_for_code)
    keyboard = get_cancel_add_channel_keyboard(lang_code) # Используем ту же клавиатуру отмены
    await message.answer(get_text(lang_code, 'promo_enter_code_prompt'), reply_markup=keyboard)

@router.message(PromoCodeActivation.waiting_for_code)
async def process_promo_code(message: Message, state: FSMContext, db_pool: asyncpg.Pool):
    lang_code = await get_user_language(message.from_user.id, db_pool)
    promo_code = message.text.strip()
    user_id = message.from_user.id

    async with db_pool.acquire() as conn:
        async with conn.transaction(): # Используем транзакцию для безопасности
            # Ищем активный промокод с оставшимися использованиями
            code_data = await conn.fetchrow(
                "SELECT * FROM promo_codes WHERE promo_code = $1 AND is_active = TRUE AND uses_left > 0 FOR UPDATE",
                promo_code
            )
            
            if not code_data:
                await message.reply(get_text(lang_code, 'promo_not_found_or_expired'))
                await state.clear()
                return

            # Уменьшаем количество использований
            await conn.execute("UPDATE promo_codes SET uses_left = uses_left - 1 WHERE id = $1", code_data['id'])
            
            # Начисляем генерации
            generations_awarded = code_data['generations_awarded']
            await conn.execute(
                """
                INSERT INTO subscriptions (user_id, generations_left) VALUES ($1, $2)
                ON CONFLICT (user_id) DO UPDATE SET generations_left = subscriptions.generations_left + EXCLUDED.generations_left;
                """,
                user_id, generations_awarded
            )

    await message.reply(get_text(lang_code, 'promo_success', count=generations_awarded))
    await state.clear()

@router.callback_query(PromoCodeActivation.waiting_for_code, F.data == "cancel_add_channel")
async def cancel_promo_activation(callback: CallbackQuery, state: FSMContext):
    lang_code = await get_user_language(callback.from_user.id, state.storage)
    await state.clear()
    await callback.message.edit_text(get_text(lang_code, 'action_cancelled'))
    await callback.answer()