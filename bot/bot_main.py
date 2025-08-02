import asyncio
import logging
import os
from dotenv import load_dotenv

import asyncpg
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

from bot.handlers import start, channels, subscription

# Загружаем переменные окружения из .env файла
load_dotenv()

async def create_db_connection_pool():
    """Создает пул подключений к базе данных."""
    return await asyncpg.create_pool(
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME"),
        host=os.getenv("DB_HOST"),
    )

async def on_startup(pool: asyncpg.Pool):
    """Выполняет действия при старте бота, например, создает таблицы в БД."""
    async with pool.acquire() as connection:
        # Таблица пользователей
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                user_id BIGINT UNIQUE NOT NULL,
                username VARCHAR(255),
                language_code VARCHAR(10),
                registration_date TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)
        # Таблица папок
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS folders (
                id SERIAL PRIMARY KEY,
                owner_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                folder_name VARCHAR(100) NOT NULL,
                UNIQUE(owner_id, folder_name)
            );
        """)
        # Таблица каналов
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS channels (
                id SERIAL PRIMARY KEY,
                channel_id BIGINT UNIQUE NOT NULL,
                channel_name VARCHAR(255) NOT NULL,
                owner_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                folder_id INTEGER REFERENCES folders(id) ON DELETE SET NULL,
                style_passport TEXT,
                style_passport_updated_at TIMESTAMP WITH TIME ZONE,
                added_date TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)
        # Таблица ИИ-профилей (больше не используется, но оставим на будущее)
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS ai_profiles (
                id SERIAL PRIMARY KEY,
                owner_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                profile_name VARCHAR(100) NOT NULL,
                activity_description TEXT,
                style_passport TEXT,
                search_scenarios TEXT,
                created_date TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(owner_id, profile_name)
            );
        """)
        # Таблица подписок
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                user_id BIGINT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
                plan_name VARCHAR(50) NOT NULL DEFAULT 'free',
                expires_at TIMESTAMP WITH TIME ZONE,
                generations_left INTEGER NOT NULL DEFAULT 3,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)
    logging.info("Database tables are ready.")

async def main():
    """Основная функция для запуска бота."""
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    defaults = DefaultBotProperties(parse_mode="HTML")
    bot = Bot(token=os.getenv("BOT_TOKEN"), default=defaults)
    
    dp = Dispatcher()

    # Создание пула подключений к БД
    db_pool = await create_db_connection_pool()
    await on_startup(db_pool)

    # Передаем пул подключений в хэндлеры через middleware
    dp['db_pool'] = db_pool

    # Подключение роутеров
    dp.include_router(start.router)
    dp.include_router(channels.router)
    dp.include_router(subscription.router)

    # Удаление старых вебхуков и запуск polling
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())