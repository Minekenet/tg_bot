from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
import asyncpg

from bot.utils.localization import get_text
from bot.utils.states import PromoCodeActivation
from bot.keyboards.inline import get_cancel_add_channel_keyboard
from bot.handlers.start import show_main_menu

router = Router()

async def get_user_language(user_id: int, db_pool: asyncpg.Pool) -> str:
    async with db_pool.acquire() as connection:
        return await connection.fetchval("SELECT language_code FROM users WHERE user_id = $1", user_id) or 'ru'

@router.message(Command("promo"))
async def promo_command_handler(message: Message, state: FSMContext, db_pool: asyncpg.Pool):
    lang_code = await get_user_language(message.from_user.id, db_pool)
    await state.set_state(PromoCodeActivation.waiting_for_code)
    keyboard = get_cancel_add_channel_keyboard(lang_code)
    await message.answer(get_text(lang_code, 'promo_enter_code_prompt'), reply_markup=keyboard)

@router.message(PromoCodeActivation.waiting_for_code)
async def process_promo_code(message: Message, state: FSMContext, db_pool: asyncpg.Pool):
    lang_code = await get_user_language(message.from_user.id, db_pool)
    promo_code_text = message.text.strip()
    user_id = message.from_user.id

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            # Шаг 1: Найти активный промокод
            code_data = await conn.fetchrow(
                "SELECT * FROM promo_codes WHERE promo_code = $1 AND is_active = TRUE AND uses_left > 0 FOR UPDATE",
                promo_code_text
            )
            
            if not code_data:
                await message.reply(get_text(lang_code, 'promo_not_found_or_expired'))
                await state.clear()
                return
            
            promo_code_id = code_data['id']

            # Шаг 2: Проверить, не активировал ли пользователь этот промокод ранее
            activation_exists = await conn.fetchval(
                "SELECT 1 FROM promo_code_activations WHERE user_id = $1 AND promo_code_id = $2",
                user_id, promo_code_id
            )

            if activation_exists:
                await message.reply(get_text(lang_code, 'promo_already_activated'))
                await state.clear()
                return

            # Шаг 3: Гарантируем, что у пользователя есть запись в подписках.
            # Если записи нет, она создастся со значением по умолчанию (3).
            await conn.execute(
                "INSERT INTO subscriptions (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING",
                user_id
            )

            # Шаг 4: Начисляем генерации
            generations_awarded = code_data['generations_awarded']
            await conn.execute(
                "UPDATE subscriptions SET generations_left = generations_left + $1, updated_at = NOW() WHERE user_id = $2",
                generations_awarded, user_id
            )

            # Шаг 5: Уменьшаем лимит промокода и записываем факт активации
            await conn.execute("UPDATE promo_codes SET uses_left = uses_left - 1 WHERE id = $1", promo_code_id)
            await conn.execute(
                "INSERT INTO promo_code_activations (user_id, promo_code_id) VALUES ($1, $2)",
                user_id, promo_code_id
            )

    await message.reply(get_text(lang_code, 'promo_success', count=generations_awarded))
    await state.clear()


@router.callback_query(PromoCodeActivation.waiting_for_code, F.data == "cancel_add_channel")
async def cancel_promo_activation(callback: CallbackQuery, state: FSMContext, db_pool: asyncpg.Pool):
    lang_code = await get_user_language(callback.from_user.id, db_pool)
    await state.clear()
    await callback.answer(get_text(lang_code, 'action_cancelled'), show_alert=False)
    await show_main_menu(callback, lang_code)