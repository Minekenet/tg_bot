import os
import datetime
from dotenv import load_dotenv

import asyncpg
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, LabeledPrice, PreCheckoutQuery, SuccessfulPayment

from bot.utils.localization import get_text
from bot.keyboards.inline import get_subscription_keyboard # Создадим ниже

load_dotenv()
router = Router()

# --- Тарифы (чтобы легко менять в одном месте) ---
PLANS = {
    "basic": {"price": 450, "generations": 30},
    "pro": {"price": 1390, "generations": 150}
}

async def get_user_language(user_id: int, db_pool: asyncpg.Pool) -> str:
    async with db_pool.acquire() as connection:
        return await connection.fetchval("SELECT language_code FROM users WHERE user_id = $1", user_id) or 'ru'

# --- ГЛАВНЫЙ ОБРАБОТЧИК МЕНЮ ПОДПИСКИ ---
@router.callback_query(F.data == "subscription")
async def subscription_menu_handler(callback: CallbackQuery, db_pool: asyncpg.Pool):
    user_id = callback.from_user.id
    lang_code = await get_user_language(user_id, db_pool)
    
    # Получаем клавиатуру, которая уже содержит информацию о текущем тарифе
    keyboard, text = await get_subscription_keyboard(user_id, lang_code, db_pool)
    
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()

# --- ОБРАБОТЧИКИ ПОКУПКИ ---

@router.callback_query(F.data.startswith("subscribe_"))
async def subscribe_handler(callback: CallbackQuery, bot: Bot, db_pool: asyncpg.Pool):
    user_id = callback.from_user.id
    lang_code = await get_user_language(user_id, db_pool)
    plan_name = callback.data.split("_")[1]
    
    if plan_name not in PLANS:
        return

    plan_info = PLANS[plan_name]
    
    # --- ИЗМЕНЕНИЕ ЗДЕСЬ ---
    # Мы полностью убираем аргумент provider_token
    await bot.send_invoice(
        chat_id=user_id,
        title=get_text(lang_code, 'invoice_title', plan_name=get_text(lang_code, f'plan_{plan_name}_name')),
        description=get_text(lang_code, 'invoice_description'),
        payload=f"monthly_subscription_{plan_name}",
        currency="XTR", # Telegram Stars
        prices=[LabeledPrice(label=f"Подписка {plan_name.capitalize()}", amount=plan_info["price"])]
    )
    await callback.answer()

# --- ОБРАБОТЧИКИ ПЛАТЕЖЕЙ TELEGRAM ---
@router.pre_checkout_query()
async def pre_checkout_query_handler(pre_checkout_query: PreCheckoutQuery, bot: Bot):
    """Подтверждает, что бот готов обработать платеж."""
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@router.message(F.successful_payment)
async def successful_payment_handler(message: Message, db_pool: asyncpg.Pool):
    """Обрабатывает успешный платеж."""
    user_id = message.from_user.id
    lang_code = await get_user_language(user_id, db_pool)
    
    payload = message.successful_payment.invoice_payload
    plan_name = payload.split("_")[2]
    
    if plan_name not in PLANS:
        return

    plan_info = PLANS[plan_name]
    new_expires_at = datetime.datetime.now() + datetime.timedelta(days=30)
    
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO subscriptions (user_id, plan_name, expires_at, generations_left, updated_at)
            VALUES ($1, $2, $3, $4, NOW())
            ON CONFLICT (user_id) DO UPDATE SET
                plan_name = EXCLUDED.plan_name,
                expires_at = EXCLUDED.expires_at,
                generations_left = EXCLUDED.generations_left,
                updated_at = NOW();
            """,
            user_id, plan_name, new_expires_at, plan_info["generations"]
        )
    
    await message.answer(
        f"{get_text(lang_code, 'payment_successful')}\n"
        f"{get_text(lang_code, 'your_plan_updated_to', plan_name=get_text(lang_code, f'plan_{plan_name}_name'))}"
    )