import asyncpg
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, LabeledPrice, PreCheckoutQuery, SuccessfulPayment

from bot.utils.localization import get_text
from bot.keyboards.inline import get_subscription_keyboard

router = Router()

# Пакеты генераций (чтобы легко менять в одном месте)
PLANS = {
    "pack5":   {"price": 50, "generations": 5},
    "pack30":  {"price": 450, "generations": 30},
    "pack150": {"price": 1390, "generations": 150}
}

async def get_user_language(user_id: int, db_pool: asyncpg.Pool) -> str:
    async with db_pool.acquire() as connection:
        return await connection.fetchval("SELECT language_code FROM users WHERE user_id = $1", user_id) or 'ru'

# ГЛАВНЫЙ ОБРАБОТЧИК МЕНЮ ПОДПИСКИ
@router.callback_query(F.data == "subscription")
async def subscription_menu_handler(callback: CallbackQuery, db_pool: asyncpg.Pool):
    user_id = callback.from_user.id
    lang_code = await get_user_language(user_id, db_pool)
    
    keyboard, text = await get_subscription_keyboard(user_id, lang_code, db_pool)
    
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()

# ОБРАБОТЧИКИ ПОКУПКИ
@router.callback_query(F.data.startswith("buy_pack_"))
async def buy_pack_handler(callback: CallbackQuery, bot: Bot, db_pool: asyncpg.Pool):
    user_id = callback.from_user.id
    lang_code = await get_user_language(user_id, db_pool)
    plan_key = callback.data.split("buy_pack_")[1]
    
    if plan_key not in PLANS:
        return

    plan_info = PLANS[plan_key]
    
    await bot.send_invoice(
        chat_id=user_id,
        title=get_text(lang_code, f'plan_{plan_key}_name'),
        description=get_text(lang_code, f'plan_{plan_key}_desc'),
        payload=f"buy_generations_{plan_key}",
        currency="XTR", # Telegram Stars
        prices=[LabeledPrice(label=get_text(lang_code, f'plan_{plan_key}_name'), amount=plan_info["price"])]
    )
    await callback.answer()

# ОБРАБОТЧИКИ ПЛАТЕЖЕЙ TELEGRAM
@router.pre_checkout_query()
async def pre_checkout_query_handler(pre_checkout_query: PreCheckoutQuery, bot: Bot):
    """Подтверждает, что бот готов обработать платеж."""
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@router.message(F.successful_payment)
async def successful_payment_handler(message: Message, db_pool: asyncpg.Pool):
    """Обрабатывает успешный платеж, добавляя генерации к балансу."""
    user_id = message.from_user.id
    lang_code = await get_user_language(user_id, db_pool)
    
    payload = message.successful_payment.invoice_payload
    plan_key = payload.split("_")[2]
    
    if plan_key not in PLANS:
        return

    plan_info = PLANS[plan_key]
    generations_to_add = plan_info["generations"]
    
    async with db_pool.acquire() as conn:
        # Просто добавляем купленное количество генераций к текущему балансу
        await conn.execute(
            """
            UPDATE subscriptions 
            SET generations_left = generations_left + $1, updated_at = NOW()
            WHERE user_id = $2
            """,
            generations_to_add, user_id
        )
    
    await message.answer(
        f"{get_text(lang_code, 'payment_successful')}\n"
        f"{get_text(lang_code, 'generations_added_success', count=generations_to_add)}"
    )