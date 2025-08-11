# bot/utils/subscription_check.py

import asyncpg

async def check_and_decrement_limit(user_id: int, db_pool: asyncpg.Pool) -> bool:
    """
    Проверяет лимиты пользователя. Если генерации есть, уменьшает счетчик и возвращает True.
    В противном случае возвращает False.
    
    ВНИМАНИЕ: Эта функция оставлена для совместимости, но для сценариев
    используются has_generations и decrement_generation_limit.
    """
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            subscription = await conn.fetchrow("SELECT * FROM subscriptions WHERE user_id = $1", user_id)
            
            if not subscription:
                await conn.execute(
                    "INSERT INTO subscriptions (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING",
                    user_id
                )
                subscription = await conn.fetchrow("SELECT * FROM subscriptions WHERE user_id = $1", user_id)

            if subscription['generations_left'] > 0:
                await conn.execute("UPDATE subscriptions SET generations_left = generations_left - 1, updated_at = NOW() WHERE user_id = $1", user_id)
                return True
            else:
                return False

# НОВАЯ ФУНКЦИЯ
async def has_generations(user_id: int, db_pool: asyncpg.Pool) -> bool:
    """
    Проверяет, есть ли у пользователя доступные генерации (> 0).
    НЕ списывает их.
    """
    async with db_pool.acquire() as conn:
        # Убедимся, что запись для пользователя существует
        await conn.execute(
            "INSERT INTO subscriptions (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING",
            user_id
        )
        generations_left = await conn.fetchval(
            "SELECT generations_left FROM subscriptions WHERE user_id = $1",
            user_id
        )
        return generations_left > 0

# НОВАЯ ФУНКЦИЯ
async def decrement_generation_limit(user_id: int, db_pool: asyncpg.Pool):
    """
    Безусловно уменьшает счетчик генераций на 1.
    Вызывается только после успешного выполнения задачи.
    """
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE subscriptions SET generations_left = generations_left - 1, updated_at = NOW() WHERE user_id = $1 AND generations_left > 0",
            user_id
        )