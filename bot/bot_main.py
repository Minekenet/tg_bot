import asyncio
import logging
import asyncpg
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

# Импортируем наш централизованный конфиг
from bot import config

# Импортируем все роутеры
from bot.handlers import start, channels, subscription, scenarios, admin, support

# Импортируем утилиты и middleware
from bot.utils.scheduler import setup_scheduler
from bot.middlewares.throttling import ThrottlingMiddleware

async def create_db_connection_pool():
    """Создает пул подключений к базе данных."""
    return await asyncpg.create_pool(
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        database=config.DB_NAME,
        host=config.DB_HOST,
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
                activity_description TEXT,
                generation_language VARCHAR(50),
                added_date TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)
        # Таблица подписок
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                user_id BIGINT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
                generations_left INTEGER NOT NULL DEFAULT 3,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)
        # Таблица для сценариев авто-постинга
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS posting_scenarios (
                id SERIAL PRIMARY KEY,
                owner_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                channel_id BIGINT NOT NULL REFERENCES channels(channel_id) ON DELETE CASCADE,
                scenario_name VARCHAR(100) NOT NULL,
                is_active BOOLEAN DEFAULT TRUE,
                keywords TEXT,
                sources TEXT,
                media_strategy VARCHAR(50) DEFAULT 'text_plus_media',
                posting_mode VARCHAR(50) DEFAULT 'direct', -- 'direct' or 'moderation'
                run_times TEXT, -- Comma-separated times, e.g., '09:00,18:30'
                timezone VARCHAR(50) DEFAULT 'UTC',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(channel_id, scenario_name)
            );
        """)
        # Таблица для предотвращения дубликатов
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS published_posts (
                id SERIAL PRIMARY KEY,
                channel_id BIGINT NOT NULL,
                source_url_hash VARCHAR(64) NOT NULL,
                published_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(channel_id, source_url_hash)
            );
        """)
    logging.info("Database tables are ready.")

async def main():
    """Основная функция для запуска бота."""
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    defaults = DefaultBotProperties(parse_mode="HTML")
    bot = Bot(token=config.BOT_TOKEN, default=defaults)
    
    dp = Dispatcher()

    # Регистрируем Throttling Middleware на все типы обновлений
    dp.update.middleware(ThrottlingMiddleware())

    db_pool = await create_db_connection_pool()
    await on_startup(db_pool)

    dp['db_pool'] = db_pool

    scheduler = await setup_scheduler(db_pool)
    scheduler.start()
    
    dp['scheduler'] = scheduler

    # Регистрируем все наши роутеры
    dp.include_router(admin.router)
    dp.include_router(start.router)
    dp.include_router(channels.router)
    dp.include_router(subscription.router)
    dp.include_router(support.router)
    dp.include_router(scenarios.router)

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())