# bot/utils/scheduler.py

import json
import hashlib
import asyncpg
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.triggers.cron import CronTrigger
import logging

from bot import config
from bot.utils.subscription_check import has_generations, decrement_generation_limit
from bot.utils.search_engine import search_news
from bot.utils.ai_generator import generate_content_robust
from bot.utils.image_handler import find_creative_commons_image_url
from bot.utils.article_parser import get_article_text
from bot.keyboards.inline import get_moderation_keyboard
from bot.utils.localization import get_text

async def generate_content_robust_with_logging(prompt: str, scenario_id: int) -> tuple[bool, str]:
    success, result = await generate_content_robust(prompt)
    if not success:
        logging.error(f"Сценарий #{scenario_id}: Ошибка генерации ИИ: {result}")
    return success, result

async def process_scenario_job(scenario_id: int, user_id: int, channel_id: int):
    logging.info(f"--- ЗАПУСК ЗАДАЧИ ДЛЯ СЦЕНАРИЯ #{scenario_id} ---")
    
    bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    db_pool = None
    lang_code = 'ru'
    
    try:
        db_pool = await asyncpg.create_pool(
            user=config.DB_USER, password=config.DB_PASSWORD,
            database=config.DB_NAME, host=config.DB_HOST
        )

        lang_code = await db_pool.fetchval("SELECT language_code FROM users WHERE user_id = $1", user_id) or 'ru'
        
        # Шаг 0. Просто проверяем наличие генераций, НЕ СПИСЫВАЯ
        can_generate = await has_generations(user_id, db_pool)
        if not can_generate:
            msg = get_text(lang_code, 'limit_exceeded_error_job')
            await bot.send_message(user_id, msg)
            logging.warning(f"Сценарий #{scenario_id}: Лимит генераций исчерпан. Задача не запущена.")
            return

        scenario = await db_pool.fetchrow("SELECT * FROM posting_scenarios WHERE id = $1", scenario_id)
        channel = await db_pool.fetchrow("SELECT * FROM channels WHERE channel_id = $1", channel_id)
        if not scenario or not channel: 
            logging.warning(f"Сценарий #{scenario_id}: Сценарий или канал не найдены в БД.")
            return

        # 1. Извлечение контекстных ключевых слов
        context_keywords = []
        if channel.get('activity_description'):
            context_prompt = f"""
            Проанализируй описание Telegram-канала. Выдели 1-3 самых главных ключевых слова, определяющих его тематику.
            Верни только эти слова через запятую. Например: "игры, гейминг, новости игр".
            
            Описание канала: "{channel.get('activity_description')}"
            """
            success, context_text = await generate_content_robust_with_logging(context_prompt, scenario_id)
            if success:
                context_keywords = [k.strip() for k in context_text.split(',') if k.strip()]
                logging.info(f"Сценарий #{scenario_id}: Извлечен контекст для поиска: {context_keywords}")

        # 2. Поиск новостей
        keywords = [k.strip() for k in scenario['keywords'].split(',')]
        sources = [s.strip() for s in scenario['sources'].split(',')]
        found_news = await search_news(keywords, sources, context_keywords)
        
        if not found_news:
            logging.info(f"Сценарий #{scenario_id}: Поиск не дал результатов.")
            await bot.send_message(user_id, get_text(lang_code, 'no_news_found_job_error', scenario_name=scenario['scenario_name']))
            return

        # 3. Фильтрация дубликатов
        links_to_check = [item['link'] for item in found_news if item.get('link')]
        if not links_to_check: return
        hashes_to_check = [hashlib.sha256(link.encode()).hexdigest() for link in links_to_check]
        query = "SELECT source_url_hash FROM published_posts WHERE channel_id = $1 AND source_url_hash = ANY($2::varchar[])"
        published_hashes = await db_pool.fetch(query, channel_id, hashes_to_check)
        published_set = {record['source_url_hash'] for record in published_hashes}
        unique_news = [item for item in found_news if item.get('link') and hashlib.sha256(item['link'].encode()).hexdigest() not in published_set]
        
        if not unique_news:
            logging.info(f"Сценарий #{scenario_id}: Найдены новости, но все они уже были опубликованы.")
            await bot.send_message(user_id, get_text(lang_code, 'no_unique_news_found_job_error', scenario_name=scenario['scenario_name']))
            return

        # 4. Выбор лучшей новости с помощью ИИ
        news_for_analysis = "\n".join([f"- Title: {item['title']}\n  Snippet: {item['snippet']}\n  Link: {item['link']}" for item in unique_news[:10]])
        analysis_prompt = f"""
        Ты - редактор новостей. Проанализируй этот список свежих новостей. Выбери ОДНУ самую важную и интересную, которая соответствует теме '{scenario.get('theme', 'общие новости')}'.
        Верни ответ СТРОГО в формате JSON с ключами "title", "link", "snippet". Новости для анализа: {news_for_analysis}
        """
        success, best_news_json = await generate_content_robust_with_logging(analysis_prompt, scenario_id)
        if not success: 
            await bot.send_message(user_id, get_text(lang_code, 'error_ai_selection', error=best_news_json))
            return
            
        try:
            best_news = json.loads(best_news_json.strip().replace("```json", "").replace("```", ""))
            selected_url = best_news.get('link')
            selected_title = best_news.get('title')
            selected_snippet = best_news.get('snippet')
        except json.JSONDecodeError: 
            await bot.send_message(user_id, get_text(lang_code, 'error_ai_selection', error="Неверный JSON-формат ответа ИИ."))
            return
        
        if not selected_url:
            await bot.send_message(user_id, get_text(lang_code, 'error_ai_selection', error="ИИ не смог выбрать подходящую ссылку."))
            return

        # 5. Извлечение текста статьи
        article_text = await get_article_text(selected_url)
        if not article_text:
            logging.warning(f"Сценарий #{scenario_id}: Не удалось извлечь текст статьи, используя сниппет.")
            article_text = selected_snippet

        # 6. Генерация запроса для изображения
        image_url = None
        if scenario['media_strategy'] == 'text_plus_media':
            image_query_prompt = f"""
            Тема сценария: {scenario.get('theme', '')}
            Заголовок статьи: {selected_title}
            
            Сгенерируй ОДИН запрос для поиска изображения, которое соответствует этой статье. 
            Запрос должен быть на языке генерации ({channel.get('generation_language') or 'русский'}) и не содержать более 10 слов. 
            Примеры: "Искусственный интеллект", "Новостные тренды", "Бизнес встреча", "Bitcoin market crash".
            Верни только сам запрос.
            """
            success_img, image_query = await generate_content_robust_with_logging(image_query_prompt, scenario_id)
            if success_img:
                image_url = await find_creative_commons_image_url(image_query)

        # 7. Генерация поста
        generation_prompt = f"""
        Ты - эксперт по контент-маркетингу и редактор Telegram-канала. Напиши пост для Telegram-канала на основе предоставленного материала.

        ---
        МАТЕРИАЛ:
        {article_text}
        ---

        ДАННЫЕ КАНАЛА:
        - Язык генерации: {channel.get('generation_language') or 'русский'}
        - Описание канала (тема и фокус): {channel.get('activity_description')}
        - Паспорт стиля (TONE OF VOICE, структура, формат): {channel.get('style_passport')}
        - Ссылка на источник: {selected_url}

        ИНСТРУКЦИИ:
        - Создай новый, уникальный текст, который пересказывает суть материала.
        - СТРОГО СЛЕДУЙ ПАСПОРТУ СТИЛЯ.
        - Используй форматирование Telegram (<b>, <i>, <a href="...">, <code>, <pre>). Ответ должен быть в формате HTML.
        - В конце поста обязательно добавь ссылку на источник.
        - Пост должен быть компактным и цепляющим.
        """
        success, post_text = await generate_content_robust_with_logging(generation_prompt, scenario_id)
        if not success: 
            await bot.send_message(user_id, get_text(lang_code, 'error_ai_generation', error=post_text))
            return
            
        # 8. Публикация/модерация и ТОЛЬКО ПОТОМ списание
        if scenario['posting_mode'] == 'moderation':
            keyboard = get_moderation_keyboard(lang_code, channel_id)
            if image_url and scenario['media_strategy'] == 'text_plus_media':
                await bot.send_photo(chat_id=user_id, photo=image_url, caption=post_text, reply_markup=keyboard)
            else:
                await bot.send_message(chat_id=user_id, text=post_text, reply_markup=keyboard)
            
            await decrement_generation_limit(user_id, db_pool)
            logging.info(f"Сценарий #{scenario_id}: Пост отправлен на модерацию. Генерация списана с пользователя {user_id}.")
            
        else: # posting_mode == 'direct'
            if image_url and scenario['media_strategy'] == 'text_plus_media':
                await bot.send_photo(chat_id=channel_id, photo=image_url, caption=post_text)
            else:
                await bot.send_message(chat_id=channel_id, text=post_text)
            
            await decrement_generation_limit(user_id, db_pool)
            logging.info(f"Сценарий #{scenario_id}: ОПУБЛИКОВАН ПОСТ. Генерация списана с пользователя {user_id}.")
            
            link_hash = hashlib.sha256(selected_url.encode()).hexdigest()
            await db_pool.execute("INSERT INTO published_posts (channel_id, source_url_hash) VALUES ($1, $2)", channel_id, link_hash)

    except Exception as e:
        logging.critical(f"Критическая ошибка в scheduled job #{scenario_id}: {e}", exc_info=True)
        try:
            await bot.send_message(user_id, get_text(lang_code, 'generic_error_in_job'))
        except Exception as send_e:
            logging.error(f"Не удалось уведомить пользователя {user_id} об ошибке: {send_e}", exc_info=True)
            
    finally:
        if db_pool:
            await db_pool.close()
        await bot.session.close()
        logging.info(f"--- ЗАДАЧА ДЛЯ СЦЕНАРИЯ #{scenario_id} ЗАВЕРШЕНА ---")

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
                kwargs={
                    "scenario_id": scenario['id'],
                    "user_id": scenario['owner_id'],
                    "channel_id": scenario['channel_id'],
                }
            )
            print(f"Задача '{job_id}' запланирована на {t} ({scenario.get('timezone', 'UTC')}).")
        except (ValueError, IndexError):
            print(f"Ошибка: неверный формат времени '{t}' для сценария #{scenario['id']}.")

def remove_job_from_scheduler(scheduler: AsyncIOScheduler, scenario: dict):
    if not scenario.get('run_times'): return
    times = [t.strip() for t in scenario['run_times'].split(',') if t.strip()]
    for t in times:
        try:
            hour, minute = map(int, t.split(':'))
            job_id = f"scenario_{scenario['id']}_{hour}_{minute}"
            if scheduler.get_job(job_id):
                scheduler.remove_job(job_id)
                print(f"Задача '{job_id}' удалена из планировщика.")
        except Exception as e:
            print(f"Не удалось удалить задачу {t} для сценария #{scenario['id']}: {e}")

async def setup_scheduler(db_pool: asyncpg.Pool) -> AsyncIOScheduler:
    jobstores = {'default': SQLAlchemyJobStore(url='sqlite:///jobs.sqlite')}
    executors = {'default': AsyncIOExecutor()}
    scheduler = AsyncIOScheduler(jobstores=jobstores, executors=executors)
    
    print("Загрузка активных сценариев в планировщик...")
    async with db_pool.acquire() as conn:
        active_scenarios = await conn.fetch("SELECT * FROM posting_scenarios WHERE is_active = TRUE")
        for scenario in active_scenarios:
            add_job_to_scheduler(scheduler, dict(scenario))
    
    print("Планировщик настроен и готов к работе.")
    return scheduler