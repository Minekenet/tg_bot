import asyncpg

async def check_and_decrement_limit(user_id: int, db_pool: asyncpg.Pool) -> bool:
    """
    Проверяет лимиты пользователя. Если генерации есть, уменьшает счетчик и возвращает True.
    В противном случае возвращает False.
    """
    async with db_pool.acquire() as conn:
        # Используем одну транзакцию для всех операций
        async with conn.transaction():
            # Получаем подписку. Если ее нет, создаем запись с бесплатными генерациями.
            subscription = await conn.fetchrow("SELECT * FROM subscriptions WHERE user_id = $1", user_id)
            
            if not subscription:
                # Создаем запись с 3 бесплатными генерациями по умолчанию
                await conn.execute(
                    "INSERT INTO subscriptions (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING",
                    user_id
                )
                subscription = await conn.fetchrow("SELECT * FROM subscriptions WHERE user_id = $1", user_id)

            # Проверяем, остались ли генерации
            if subscription['generations_left'] > 0:
                # Уменьшаем счетчик и возвращаем успех
                await conn.execute("UPDATE subscriptions SET generations_left = generations_left - 1, updated_at = NOW() WHERE user_id = $1", user_id)
                return True
            else:
                # Лимит исчерпан
                return False