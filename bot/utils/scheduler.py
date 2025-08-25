import json
import hashlib
import logging
import asyncpg
from aiogram import Bot
from aiogram.exceptions import TelegramNetworkError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.triggers.cron import CronTrigger
import xml.etree.ElementTree as ET
import uuid # Импортируем uuid для генерации уникальных ID
from tenacity import retry, wait_exponential, stop_after_attempt, before_sleep_log
from aiohttp import ClientConnectorError
import re # Импортируем re для регулярных выражений

from bot import config
from bot.utils.subscription_check import has_generations, decrement_generation_limit
from bot.utils.search_engine import search_news
from bot.utils.image_handler import find_creative_commons_image_url
from bot.utils.article_parser import get_article_text
from bot.utils.ai_generator import generate_post_via_sonar
from bot.keyboards.inline import get_moderation_keyboard
from bot.utils.localization import get_text, escape_html
from bot.config import SEARCH_QUERY_COST, AI_TOKEN_COST_PER_1000, BOT_TOKEN, SONAR_REQUEST_COST_RUB, AI_TOKEN_COST_PER_1M_RUB, MIN_SCENARIO_INTERVAL_MINUTES
from bot.utils.ai_generator import generate_content_robust, select_best_articles_from_search_results
from decimal import Decimal

logger = logging.getLogger(__name__)

@retry(wait=wait_exponential(multiplier=1, min=4, max=10), stop=stop_after_attempt(5), before_sleep=before_sleep_log(logger, logging.WARNING))
async def send_message_with_retry(bot, chat_id, text, reply_markup=None):
    try:
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
    except TelegramNetworkError as e:
        logger.error(f"TelegramNetworkError при отправке сообщения в чат {chat_id}: {e}")
        raise

def is_video_url(url: str) -> bool:
    """Проверяет, является ли URL ссылкой на видеохостинг."""
    video_domains = ["youtube.com", "youtu.be", "vimeo.com", "rutube.ru", "tiktok.com"]
    return any(domain in url for domain in video_domains)

async def generate_content_robust_with_logging(prompt: str, scenario_id: int, target_lang_code: str = 'ru') -> tuple[bool, str, int]:
    """
    Обёртка для generate_content_robust, которая логирует использование токенов
    и стоимость, а также обеспечивает генерацию на целевом языке.
    """
    # Здесь db_pool будет доступен через замыкание, так как setup_scheduler его передает
    # и он уже передан в process_scenario_job
    success, result, token_count = await generate_content_robust(prompt) # generate_content_robust не принимает target_lang_code
    if success and token_count > 0:
        try:
            # db_pool должен быть доступен в контексте
            await db_pool_global.execute("INSERT INTO ai_usage (scenario_id, tokens_used) VALUES ($1, $2)", scenario_id, token_count)
        except Exception as e:
            logging.error(f"Ошибка при сохранении данных об использовании AI: {e}", exc_info=True)
    return success, result, token_count

async def process_scenario_job(scenario_id: int, user_id: int, channel_id: int):
    logging.info(f"--- ЗАПУСК ЗАДАЧИ ДЛЯ СЦЕНАРИЯ #{scenario_id} ---\n")
    
    total_ai_tokens = 0
    total_search_queries = 0
    total_sonar_requests = 0
    total_image_queries = 0

    bot = Bot(token=config.BOT_TOKEN, parse_mode="HTML")
    global db_pool_global # Объявляем, что будем использовать глобальную переменную
    db_pool_global = None
    user_lang_code = 'ru' # Default language
    
    try:
        # При создании job'ов db_pool передается в kwargs
        # Если job запускается напрямую, то пул нужно создать
        if not db_pool_global:
            db_pool_global = await asyncpg.create_pool(
                user=config.DB_USER, password=config.DB_PASSWORD,
                database=config.DB_NAME, host=config.DB_HOST
            )

        db_pool = db_pool_global # Используем локальное имя для удобства
        
        # ИЗМЕНЕНО: Получаем язык ИНТЕРФЕЙСА пользователя
        user_lang_code = await db_pool.fetchval("SELECT language_code FROM users WHERE user_id = $1", user_id) or 'ru'
        
        can_generate = await has_generations(user_id, db_pool)
        if not can_generate:
            msg = get_text(user_lang_code, 'limit_exceeded_error_job', escape_html_chars=True)
            await send_message_with_retry(bot, user_id, msg)
            logging.warning(f"Сценарий #{scenario_id}: Лимит генераций исчерпан. Задача не запущена.")
            return

        scenario = await db_pool.fetchrow("SELECT * FROM posting_scenarios WHERE id = $1", scenario_id)
        channel = await db_pool.fetchrow("SELECT * FROM channels WHERE channel_id = $1", channel_id)
        if not scenario or not channel: 
            logging.warning(f"Сценарий #{scenario_id}: Сценарий или канал не найдены в БД.")
            return

        # Новый путь: Sonar заменяет поиск и парсинг
        theme = scenario.get('theme', '')
        keywords = [k.strip() for k in (scenario.get('keywords') or '').split(',') if k.strip()]

        success_sonar, sonar_data, tokens_used_sonar = await generate_post_via_sonar(
            theme,
            keywords,
            user_lang_code,
            style_passport=(channel.get('style_passport') or ''),
            activity_description=(channel.get('activity_description') or ''),
            generation_language=(channel.get('generation_language') or user_lang_code or 'ru')
        )
        total_ai_tokens += tokens_used_sonar
        total_sonar_requests += 1

        if not success_sonar:
            logging.info(f"Сценарий #{scenario_id}: Sonar не нашел подходящую свежую новость или вернул ошибку: {sonar_data}")
            await send_message_with_retry(bot, user_id, get_text(user_lang_code, 'no_news_found_job_error', scenario_name=scenario['scenario_name'], escape_html_chars=True))
            return

        final_article_url = sonar_data.get('source_url') or ''
        post_title = sonar_data.get('title') or ''
        post_body = sonar_data.get('body') or ''
        image_query = sonar_data.get('image_query') or ''

        if not final_article_url or not (post_title or post_body):
            logging.warning(f"Сценарий #{scenario_id}: Sonar вернул неполные данные: {sonar_data}")
            await send_message_with_retry(bot, user_id, get_text(user_lang_code, 'generic_error_in_job', escape_html_chars=True))
            return

        # Проверка на дубликаты источника
        link_hash = hashlib.sha256(final_article_url.encode()).hexdigest()
        query = "SELECT source_url_hash FROM published_posts WHERE channel_id = $1 AND source_url_hash = $2"
        already_published = await db_pool.fetchval(query, channel_id, link_hash)
        if already_published:
            logging.info(f"Сценарий #{scenario_id}: Выбранная Sonar статья уже была опубликована: {final_article_url}.")
            await send_message_with_retry(bot, user_id, get_text(user_lang_code, 'no_unique_news_found_job_error', scenario_name=scenario['scenario_name'], escape_html_chars=True))
            return

        # Проверка на дубликаты после выбора и парсинга
        link_hash = hashlib.sha256(final_article_url.encode()).hexdigest()
        query = "SELECT source_url_hash FROM published_posts WHERE channel_id = $1 AND source_url_hash = $2"
        already_published = await db_pool.fetchval(query, channel_id, link_hash)

        if already_published:
            logging.info(f"Сценарий #{scenario_id}: Выбранная статья уже была опубликована: {final_article_url}.")
            await send_message_with_retry(bot, user_id, get_text(user_lang_code, 'no_unique_news_found_job_error', scenario_name=scenario['scenario_name'], escape_html_chars=True))
            return

        image_url = None
        channel_generation_language = channel.get('generation_language') or 'ru' # Default to Russian

        # Теперь пост уже сгенерирован Sonar, только формируем финальный текст
        post_text = f"<b>{post_title}</b>\n\n{post_body}" if post_title else post_body

        # Если стратегия "Текст + Медиа" и ИИ сгенерировал запрос изображения, ищем изображение
        if scenario['media_strategy'] == 'text_plus_media' and image_query:
            logging.debug(f"Сценарий #{scenario_id}: Сгенерированный запрос для изображения: {image_query}")
            image_url = await find_creative_commons_image_url(image_query, channel_generation_language)
            total_image_queries += 1 if image_url is not None else 1
            if not image_url:
                logging.warning(f"Сценарий #{scenario_id}: Не удалось найти изображение для запроса: {image_query}")
        elif scenario['media_strategy'] == 'text_plus_media' and not image_query:
            logging.warning(f"Сценарий #{scenario_id}: Стратегия 'Текст + Медиа', но ИИ не сгенерировал запрос для изображения.")

        # Если все успешно, списываем генерацию
        await decrement_generation_limit(user_id, db_pool) # Переносим сюда

        # Логируем расходы/доходы в ledger
        try:
            # Стоимость токенов: 1,000,000 токенов = 120 руб => 0.00012 руб/токен
            cost_per_token = 120 / 1_000_000
            cost_tokens_rub = total_ai_tokens * cost_per_token
            # Стоимость запросов: SEARCH_QUERY_COST за каждый поисковый/картинковый запрос
            cost_requests_rub = (total_search_queries + total_image_queries) * SEARCH_QUERY_COST
            await db_pool.execute(
                """
                INSERT INTO usage_ledger (user_id, scenario_id, kind, is_free, tokens_used, sonar_requests, image_requests, cost_tokens, cost_requests, revenue)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """,
                user_id, scenario_id, 'post', True, total_ai_tokens, total_search_queries, total_image_queries, cost_tokens_rub, cost_requests_rub, 0
            )
        except Exception as e:
            logging.error(f"Не удалось записать usage_ledger: {e}", exc_info=True)
        logging.info(f"Сценарий #{scenario_id}: Генерация успешно списана с пользователя {user_id}.")

        # Экранируем текст поста для HTML перед отправкой
        escaped_post_text = escape_html(post_text)

        # Отправка поста
        if scenario['posting_mode'] == 'moderation':
            # Генерируем уникальный ID для модерации
            moderation_id = str(uuid.uuid4())
            await db_pool.execute(
                "INSERT INTO pending_moderation_posts (moderation_id, channel_id, article_url) VALUES ($1, $2, $3)",
                moderation_id, channel_id, final_article_url
            )

            keyboard = get_moderation_keyboard(user_lang_code, channel_id, moderation_id) # Передаем moderation_id
            if image_url and scenario['media_strategy'] == 'text_plus_media':
                await bot.send_photo(chat_id=user_id, photo=image_url, caption=escaped_post_text, reply_markup=keyboard)
            else:
                await send_message_with_retry(bot, user_id, escaped_post_text, reply_markup=keyboard)
            logging.info(f"Сценарий #{scenario_id}: Пост отправлен на модерацию пользователю {user_id}. Moderation ID: {moderation_id}")
            
        else: # Режим прямой публикации
            if image_url and scenario['media_strategy'] == 'text_plus_media':
                await bot.send_photo(chat_id=channel_id, photo=image_url, caption=escaped_post_text)
            else:
                await send_message_with_retry(bot, channel_id, escaped_post_text)
            
            logging.info(f"Сценарий #{scenario_id}: ОПУБЛИКОВАН ПОСТ в канал {channel_id}. URL: {final_article_url}")
            
            # Сохраняем хеш опубликованной статьи
            await db_pool.execute("INSERT INTO published_posts (channel_id, source_url_hash) VALUES ($1, $2)", channel_id, link_hash)

    except ClientConnectorError as e:
        logging.error(f"Сценарий #{scenario_id}: Сетевая ошибка при выполнении фоновой задачи: {e}", exc_info=True)
        await send_message_with_retry(bot, user_id, get_text(user_lang_code, 'generic_error_in_job', escape_html_chars=True))
    except TelegramNetworkError as e:
        logging.error(f"Сценарий #{scenario_id}: Ошибка Telegram API при выполнении фоновой задачи: {e}", exc_info=True)
        await send_message_with_retry(bot, user_id, get_text(user_lang_code, 'generic_error_in_job', escape_html_chars=True))
    except Exception as e:
        logging.critical(f"Критическая ошибка в scheduled job #{scenario_id}: {e}", exc_info=True)
        try:
            await send_message_with_retry(bot, user_id, get_text(user_lang_code, 'generic_error_in_job', escape_html_chars=True))
        except Exception as send_e:
            logging.error(f"Не удалось уведомить пользователя {user_id} об ошибке: {send_e}", exc_info=True)
            
    finally:
        # Закрываем пул соединений, если он был создан в этой задаче
        if db_pool_global:
            await db_pool_global.close()
            db_pool_global = None # Сбрасываем глобальную переменную
        await bot.session.close()
        total_cost = (total_ai_tokens / 1000) * AI_TOKEN_COST_PER_1000 \
            + (total_sonar_requests * SONAR_REQUEST_COST_RUB) \
            + ((total_search_queries + total_image_queries) * SEARCH_QUERY_COST)
        logging.info(f"Сценарий #{scenario_id}: ИТОГО: {total_ai_tokens} токенов AI, {total_sonar_requests} Sonar-запросов, {total_search_queries} поисковых запросов, {total_image_queries} запросов изображений. Затраты: {total_cost:.2f} руб.")
        logging.info(f"--- ЗАДАЧА ДЛЯ СЦЕНАРИЯ #{scenario_id} ЗАВЕРШЕНА ---")

db_pool_global: asyncpg.Pool = None # Глобальная переменная для пула DB

def add_job_to_scheduler(scheduler: AsyncIOScheduler, scenario: dict):
    if not scenario.get('run_times'): return
    
    times = [t.strip() for t in scenario['run_times'].split(',') if t.strip()]

    # Фильтруем времена, чтобы минимальный интервал между ними был не меньше MIN_SCENARIO_INTERVAL_MINUTES
    unique_sorted = []
    def to_minutes(ts: str) -> int:
        h, m = map(int, ts.split(':'))
        return h * 60 + m
    for t in sorted(times, key=lambda x: to_minutes(x)):
        if not unique_sorted:
            unique_sorted.append(t)
        else:
            prev_m = to_minutes(unique_sorted[-1])
            cur_m = to_minutes(t)
            if cur_m - prev_m >= MIN_SCENARIO_INTERVAL_MINUTES:
                unique_sorted.append(t)
            else:
                logging.warning(f"Пропущено время запуска '{t}' для сценария #{scenario['id']} — интервал меньше {MIN_SCENARIO_INTERVAL_MINUTES} минут")

    for t in unique_sorted:
        try:
            hour, minute = map(int, t.split(':'))
            job_id = f"scenario_{scenario['id']}_{hour}_{minute}"
            scheduler.add_job(
                process_scenario_job,
                trigger=CronTrigger(hour=hour, minute=minute, second=0, timezone=scenario.get('timezone', 'UTC')),
                id=job_id,
                name=f"{scenario['scenario_name']} at {t}",
                replace_existing=True,
                kwargs={ "scenario_id": scenario['id'], "user_id": scenario['owner_id'], "channel_id": scenario['channel_id'] }
            )
            logging.info(f"Задача '{job_id}' запланирована на {t} ({scenario.get('timezone', 'UTC')}).")
        except (ValueError, IndexError):
            logging.error(f"Ошибка: неверный формат времени '{t}' для сценария #{scenario['id']}.")

def remove_job_from_scheduler(scheduler: AsyncIOScheduler, scenario: dict):
    if not scenario.get('run_times'): return
    times = [t.strip() for t in scenario['run_times'].split(',') if t.strip()]
    for t in times:
        try:
            hour, minute = map(int, t.split(':'))
            job_id = f"scenario_{scenario['id']}_{hour}_{minute}"
            if scheduler.get_job(job_id):
                scheduler.remove_job(job_id)
                logging.info(f"Задача '{job_id}' удалена из планировщика.")
        except Exception as e:
            logging.error(f"Не удалось удалить задачу {t} для сценария #{scenario['id']}: {e}")

async def setup_scheduler(db_pool: asyncpg.Pool) -> AsyncIOScheduler:
    jobstores = {'default': SQLAlchemyJobStore(url='sqlite:///jobs.sqlite')}
    executors = {'default': AsyncIOExecutor()}
    scheduler = AsyncIOScheduler(jobstores=jobstores, executors=executors)
    
    global db_pool_global # Объявляем, что будем использовать глобальную переменную
    db_pool_global = db_pool # Сохраняем переданный пул

    # Для начальной загрузки используем db_pool_global
    async with db_pool_global.acquire() as conn:
        active_scenarios = await conn.fetch("SELECT * FROM posting_scenarios WHERE is_active = TRUE")
        for scenario in active_scenarios:
            add_job_to_scheduler(scheduler, dict(scenario))
    
    logging.info("Планировщик настроен и готов к работе.")
    return scheduler