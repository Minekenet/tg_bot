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
from bot.keyboards.inline import get_moderation_keyboard
from bot.utils.localization import get_text, escape_html
from bot.config import SEARCH_QUERY_COST, AI_TOKEN_COST_PER_1000, BOT_TOKEN
from bot.utils.ai_generator import generate_content_robust, select_best_articles_from_search_results

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

        # ИЗМЕНЕНО: Логика поиска теперь чистая и правильная
        theme = scenario.get('theme', '')
        keywords = [k.strip() for k in scenario['keywords'].split(',') if k.strip()]
        
        # Передаем тему, ключевые слова и ЯЗЫК ИНТЕРФЕЙСА пользователя
        # XMLRiver всегда возвращает XML, поэтому его нужно сначала распарсить
        raw_search_results, search_count = await search_news(theme, keywords, user_lang_code)
        total_search_queries += search_count

        if not raw_search_results:
            logging.info(f"Сценарий #{scenario_id}: Поиск не дал результатов.")
            await send_message_with_retry(bot, user_id, get_text(user_lang_code, 'no_news_found_job_error', scenario_name=scenario['scenario_name'], escape_html_chars=True))
            return

        # Извлечение данных из XML в удобный формат для ИИ
        articles_for_ai_selection = []
        try:
            root = ET.fromstring(raw_search_results)
            for doc in root.findall('.//doc'):
                url = doc.find('url').text if doc.find('url') is not None else None
                title = doc.find('title').text if doc.find('title') is not None else None
                # Объединяем все <passage> в один сниппет
                passages = [p.text for p in doc.findall('passages/passage') if p.text is not None]
                snippet = " ".join(passages)

                if url and title and snippet and not is_video_url(url):
                    articles_for_ai_selection.append({
                        "url": url,
                        "title": title,
                        "passages": snippet
                    })
        except ET.ParseError as e:
            logging.error(f"Ошибка парсинга XML ответа от XMLRiver: {e}", exc_info=True)
            await send_message_with_retry(bot, user_id, get_text(user_lang_code, 'generic_error_in_job', escape_html_chars=True))
            return

        if not articles_for_ai_selection:
            logging.info(f"Сценарий #{scenario_id}: После парсинга XML не осталось статей для выбора.")
            await send_message_with_retry(bot, user_id, get_text(user_lang_code, 'no_news_found_job_error', scenario_name=scenario['scenario_name'], escape_html_chars=True))
            return

        # ИИ выбирает 3 лучшие статьи
        success_ai_selection, selected_urls, tokens_used_selection = await select_best_articles_from_search_results(
            articles_for_ai_selection, user_lang_code
        )
        total_ai_tokens += tokens_used_selection

        if not success_ai_selection or not selected_urls:
            logging.warning(f"Сценарий #{scenario_id}: ИИ не смог выбрать подходящие статьи. {selected_urls}")
            await send_message_with_retry(bot, user_id, get_text(user_lang_code, 'error_ai_selection', error="ИИ не смог выбрать подходящие статьи.", escape_html_chars=True))
            return

        article_text = None
        final_article_url = None
        # Пытаемся спарсить текст из статей по порядку
        for url in selected_urls:
            try:
                article_text = await get_article_text(url)
            except ClientConnectorError as e:
                logging.error(f"Сценарий #{scenario_id}: Сетевая ошибка при попытке спарсить статью {url}: {e}", exc_info=True)
                article_text = None # Принудительно устанавливаем None для активации логики обработки ошибки парсинга
                continue # Пробуем следующую статью
            except Exception as e:
                logging.error(f"Сценарий #{scenario_id}: Неизвестная ошибка при парсинге статьи {url}: {e}", exc_info=True)
                article_text = None # Принудительно устанавливаем None для активации логики обработки ошибки парсинга
                continue # Пробуем следующую статью

            if article_text:
                final_article_url = url
                
                # Проверка на дубликаты
                link_hash = hashlib.sha256(final_article_url.encode()).hexdigest()
                query = "SELECT source_url_hash FROM published_posts WHERE channel_id = $1 AND source_url_hash = $2"
                already_published = await db_pool.fetchval(query, channel_id, link_hash)

                if not already_published:
                    logging.info(f"Сценарий #{scenario_id}: Успешно извлечен текст из статьи и она не является дубликатом: {url}")
                    # Добавляем логирование для извлеченного текста статьи
                    logging.debug(f"Сценарий #{scenario_id}: Извлеченный текст статьи: {article_text}")
                    break # Если успешно спарсили и не дубликат, выходим
                else:
                    logging.info(f"Сценарий #{scenario_id}: Статья уже была опубликована, пробуем следующую: {url}.")
                    article_text = None # Сбрасываем, чтобы продолжить поиск
                    final_article_url = None

        if not article_text or not final_article_url:
            logging.warning(f"Сценарий #{scenario_id}: Не удалось извлечь текст ни из одной из 3 выбранных ИИ статей или все они являются дубликатами.")
            await send_message_with_retry(bot, user_id, get_text(user_lang_code, 'article_parsing_failed_job_error', scenario_name=scenario['scenario_name'], escape_html_chars=True))
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
        
        # Генерация финального поста, включая запрос на изображение, если стратегия "Текст + Медиа"
        generation_prompt = get_text(channel_generation_language, "post_generation_ai_prompt",
                                     article_text=article_text,
                                     activity_description=channel.get('activity_description', ''),
                                     style_passport=channel.get('style_passport', ''),
                                     selected_url=final_article_url, # Передаем URL в промпт, чтобы ИИ мог его включить
                                     scenario_theme=scenario.get('theme', ''),
                                     scenario_keywords=scenario.get('keywords', '')
                                     )
        
        # Добавляем логирование для промпта генерации поста
        logging.debug(f"Сценарий #{scenario_id}: Промпт для генерации поста (первые 500 символов): {generation_prompt[:500]}...")
        
        success_post, post_content_raw, tokens_used_post = await generate_content_robust_with_logging(generation_prompt, scenario_id, channel_generation_language)
        total_ai_tokens += tokens_used_post

        if not success_post:
            await send_message_with_retry(bot, user_id, get_text(user_lang_code, 'error_ai_generation', error=post_content_raw, escape_html_chars=True))
            return # НЕ СПИСЫВАЕМ ГЕНЕРАЦИЮ, ЕСЛИ ПОСТ НЕ СГЕНЕРИРОВАН

        # Парсим JSON-ответ от ИИ
        post_title = ""
        post_body = ""
        image_query = ""
        try:
            post_data = json.loads(post_content_raw)
            post_title = post_data.get('title', '')
            post_body = post_data.get('body', '')
            # Извлекаем запрос на изображение, если он есть
            image_query = post_data.get('image_query', '')
            
            # Если заголовок или тело пусты, логируем ошибку и используем необработанный текст
            if not post_title or not post_body:
                logging.warning(f"Сценарий #{scenario_id}: JSON от ИИ не содержит полей 'title' или 'body'. Используем необработанный текст.")
                post_text = post_content_raw # Используем необработанный текст, если JSON неполный
                post_title = "" # Сбрасываем заголовок
            else:
                post_text = f"<b>{post_title}</b>\n\n{post_body}"
        except json.JSONDecodeError as e:
            logging.error(f"Сценарий #{scenario_id}: Ошибка парсинга JSON ответа от ИИ: {e}. Используем необработанный текст: {post_content_raw}", exc_info=True)
            post_text = post_content_raw # Используем необработанный текст при ошибке парсинга

        # Если стратегия "Текст + Медиа" и ИИ сгенерировал запрос изображения, ищем изображение
        if scenario['media_strategy'] == 'text_plus_media' and image_query:
            logging.debug(f"Сценарий #{scenario_id}: Сгенерированный запрос для изображения: {image_query}")
            image_url = await find_creative_commons_image_url(image_query, channel_generation_language)
            if not image_url:
                logging.warning(f"Сценарий #{scenario_id}: Не удалось найти изображение для запроса: {image_query}")
        elif scenario['media_strategy'] == 'text_plus_media' and not image_query:
            logging.warning(f"Сценарий #{scenario_id}: Стратегия 'Текст + Медиа', но ИИ не сгенерировал запрос для изображения.")

        # Если все успешно, списываем генерацию
        await decrement_generation_limit(user_id, db_pool) # Переносим сюда
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
        total_cost = (total_ai_tokens / 1000) * AI_TOKEN_COST_PER_1000 + (total_search_queries * SEARCH_QUERY_COST)
        logging.info(f"Сценарий #{scenario_id}: ИТОГО: {total_ai_tokens} токенов AI, {total_search_queries} поисковых запросов. Затраты: {total_cost:.2f} руб.")
        logging.info(f"--- ЗАДАЧА ДЛЯ СЦЕНАРИЯ #{scenario_id} ЗАВЕРШЕНА ---")

db_pool_global: asyncpg.Pool = None # Глобальная переменная для пула DB

def add_job_to_scheduler(scheduler: AsyncIOScheduler, scenario: dict):
    if not scenario.get('run_times'): return
    
    times = [t.strip() for t in scenario['run_times'].split(',') if t.strip()]
    for t in times:
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