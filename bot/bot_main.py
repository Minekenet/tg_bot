import asyncio
import logging
import logging.handlers
import os
import sys
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.redis import RedisStorage
import asyncpg
import functools

from bot import config
from bot.handlers import start, channels, subscription, scenarios, admin, support, help, promo
from bot.utils.scheduler import setup_scheduler
from bot.middlewares.throttling import ThrottlingMiddleware
from bot.utils.telegram_logger import TelegramLogsHandler

def setup_logging():
    """Настраивает систему логирования для записи в файлы и отправки в Telegram."""
    log_format = "%(asctime)s - %(levelname)s - %(name)s - %(message)s"
    
    if not os.path.exists('logs'):
        os.makedirs('logs')

    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

    if logger.hasHandlers():
        logger.handlers.clear()

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(logging.Formatter(log_format))
    logger.addHandler(stream_handler)

    file_handler = logging.handlers.TimedRotatingFileHandler(
        'logs/bot.log', when='D', interval=1, backupCount=7, encoding='utf-8'
    )
    file_handler.setFormatter(logging.Formatter(log_format))
    logger.addHandler(file_handler)

    if config.ADMINS:
        telegram_handler = TelegramLogsHandler(bot_token=config.BOT_TOKEN, chat_id=config.ADMINS[0])
        telegram_handler.setLevel(logging.ERROR)
        telegram_handler.setFormatter(logging.Formatter(f"<b>Bot Alert!</b>\n<pre>{log_format}</pre>"))
        logger.addHandler(telegram_handler)

    logging.info("Logging system configured successfully.")


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
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                user_id BIGINT UNIQUE NOT NULL,
                username VARCHAR(255),
                language_code VARCHAR(10),
                registration_date TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS folders (
                id SERIAL PRIMARY KEY,
                owner_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                folder_name VARCHAR(100) NOT NULL,
                UNIQUE(owner_id, folder_name)
            );
        """)
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
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                user_id BIGINT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
                generations_left INTEGER NOT NULL DEFAULT 3,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)
        # Удален столбец `sources`
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS posting_scenarios (
                id SERIAL PRIMARY KEY,
                owner_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                channel_id BIGINT NOT NULL REFERENCES channels(channel_id) ON DELETE CASCADE,
                scenario_name VARCHAR(100) NOT NULL,
                theme TEXT,
                is_active BOOLEAN DEFAULT TRUE,
                keywords TEXT,
                media_strategy VARCHAR(50) DEFAULT 'text_plus_media',
                posting_mode VARCHAR(50) DEFAULT 'direct',
                run_times TEXT,
                timezone VARCHAR(50) DEFAULT 'UTC',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(channel_id, scenario_name)
            );
        """)
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS published_posts (
                id SERIAL PRIMARY KEY,
                channel_id BIGINT NOT NULL,
                source_url_hash VARCHAR(64) NOT NULL,
                published_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(channel_id, source_url_hash)
            );
        """)
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS promo_codes (
                id SERIAL PRIMARY KEY,
                promo_code VARCHAR(100) UNIQUE NOT NULL,
                generations_awarded INTEGER NOT NULL,
                total_uses INTEGER NOT NULL,
                uses_left INTEGER NOT NULL,
                is_active BOOLEAN DEFAULT TRUE,
                created_by BIGINT REFERENCES users(user_id),
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS promo_code_activations (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                promo_code_id INTEGER NOT NULL REFERENCES promo_codes(id) ON DELETE CASCADE,
                activated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, promo_code_id)
            );
        """)
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS ai_usage (
                id SERIAL PRIMARY KEY,
                scenario_id INTEGER REFERENCES posting_scenarios(id) ON DELETE SET NULL,
                tokens_used INTEGER NOT NULL,
                cost NUMERIC(10, 4) DEFAULT 0.0, -- Добавим поле для стоимости
                used_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS pending_moderation_posts (
                moderation_id VARCHAR(36) PRIMARY KEY, -- UUID для уникального идентификатора
                channel_id BIGINT NOT NULL,
                article_url TEXT NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)
    logging.info("Database tables are ready.")

async def on_shutdown(pool: asyncpg.Pool, scheduler):
    logging.info("Shutting down scheduler...")
    scheduler.shutdown()
    logging.info("Closing database connection pool...")
    await pool.close()
    logging.info("Database connection pool closed.")

async def main():
    """Основная функция для запуска бота."""
    setup_logging()

    storage = RedisStorage.from_url('redis://redis:6379/0')

    bot = Bot(token=config.BOT_TOKEN, parse_mode="HTML")
    
    dp = Dispatcher(storage=storage, parse_mode="HTML")
    dp.update.middleware(ThrottlingMiddleware())

    # Словарь для хранения времени последней записи ошибки
    last_error_log_time = {}
    ERROR_LOG_COOLDOWN = 60 # Секунды

    async def on_error(event, error):
        error_type = type(error).__name__
        current_time = asyncio.get_event_loop().time()

        if current_time - last_error_log_time.get(error_type, 0) > ERROR_LOG_COOLDOWN:
            logging.error(f"Необработанное исключение: {error}", exc_info=True)
            last_error_log_time[error_type] = current_time
        else:
            logging.debug(f"Игнорируем повторяющуюся ошибку ({error_type}): {error}")

    dp.errors.register(on_error)

    db_pool = await create_db_connection_pool()
    await on_startup(db_pool)

    dp['db_pool'] = db_pool
    scheduler = await setup_scheduler(dp['db_pool'])
    scheduler.start()
    dp['scheduler'] = scheduler

    dp.include_router(admin.router)
    dp.include_router(help.router)
    dp.include_router(promo.router)
    dp.include_router(start.router)
    dp.include_router(channels.router)
    dp.include_router(subscription.router)
    dp.include_router(support.router)
    dp.include_router(scenarios.router)

    dp.shutdown.register(functools.partial(on_shutdown, pool=dp['db_pool'], scheduler=dp['scheduler']))

    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Starting polling...")
    await dp.start_polling(bot)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot stopped.")
    except Exception as e:
        logging.critical(f"Bot failed to start: {e}", exc_info=True)