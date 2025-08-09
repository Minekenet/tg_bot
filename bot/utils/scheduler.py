import json
import hashlib
import asyncpg
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.triggers.cron import CronTrigger

from bot import config # Импортируем конфиг

from bot.utils.subscription_check import check_and_decrement_limit
from bot.utils.search_engine import search_news
from bot.utils.ai_generator import generate_style_passport_from_text as generate_text
from bot.utils.image_handler import find_creative_commons_image_url
from bot.utils.article_parser import get_article_text
from bot.keyboards.inline import get_moderation_keyboard
from bot.utils.localization import get_text

async def process_scenario_job(scenario_id: int, user_id: int, channel_id: int):
    print(f"--- ЗАПУСК ЗАДАЧИ ДЛЯ СЦЕНАРИЯ #{scenario_id} ---")
    
    bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    db_pool = await asyncpg.create_pool(
        user=config.DB_USER, password=config.DB_PASSWORD,
        database=config.DB_NAME, host=config.DB_HOST
    )
    lang_code = 'ru'

    try:
        lang_code = await db_pool.fetchval("SELECT language_code FROM users WHERE user_id = $1", user_id) or 'ru'
        
        can_generate = await check_and_decrement_limit(user_id, db_pool)
        if not can_generate:
            msg = get_text(lang_code, 'limit_exceeded_error')
            await bot.send_message(user_id, msg)
            return

        scenario = await db_pool.fetchrow("SELECT * FROM posting_scenarios WHERE id = $1", scenario_id)
        channel = await db_pool.fetchrow("SELECT * FROM channels WHERE channel_id = $1", channel_id)
        if not scenario or not channel: return

        context_keywords = []
        if channel.get('activity_description'):
            context_prompt = f"""
            Проанализируй описание Telegram-канала. Выдели 1-3 самых главных ключевых слова, определяющих его тематику.
            Верни только эти слова через запятую. Например: "игры, гейминг, новости игр".
            
            Описание канала: "{channel.get('activity_description')}"
            """
            success, context_text = await generate_text(context_prompt)
            if success:
                context_keywords = [k.strip() for k in context_text.split(',')]
                print(f"Извлечен контекст для поиска: {context_keywords}")

        keywords = [k.strip() for k in scenario['keywords'].split(',')]
        sources = [s.strip() for s in scenario['sources'].split(',')]
        found_news = await search_news(keywords, sources, context_keywords)
        if not found_news:
            print(f"Поиск по сценарию #{scenario_id} с учетом контекста не дал результатов.")
            return

        links_to_check = [item['link'] for item in found_news if item.get('link')]
        if not links_to_check: return
        hashes_to_check = [hashlib.sha256(link.encode()).hexdigest() for link in links_to_check]
        query = "SELECT source_url_hash FROM published_posts WHERE channel_id = $1 AND source_url_hash = ANY($2::varchar[])"
        published_hashes = await db_pool.fetch(query, channel_id, hashes_to_check)
        published_set = {record['source_url_hash'] for record in published_hashes}
        unique_news = [item for item in found_news if item.get('link') and hashlib.sha256(item['link'].encode()).hexdigest() not in published_set]
        if not unique_news:
            print(f"Найдены новости, но все они уже были опубликованы.")
            return

        news_for_analysis = "\n".join([f"- Title: {item['title']}\n  Snippet: {item['snippet']}\n  Link: {item['link']}" for item in unique_news[:10]])
        analysis_prompt = f"""
        Ты - редактор новостей. Проанализируй этот список свежих новостей. Выбери ОДНУ самую важную и интересную.
        Верни ответ СТРОГО в формате JSON с ключами "title", "link", "snippet". Новости для анализа: {news_for_analysis}
        """
        success, best_news_json = await generate_text(analysis_prompt)
        if not success: return
        try:
            best_news = json.loads(best_news_json.strip().replace("```json", "").replace("```", ""))
        except json.JSONDecodeError: return

        article_text = await get_article_text(best_news.get('link')) or best_news.get('snippet')

        generation_prompt = f"""
        Напиши пост для Telegram-канала на основе материала:
        ---
        {article_text}
        ---
        ИНСТРУКЦИИ:
        - Проанализируй материал, создай новый уникальный текст, раскрывающий суть.
        - Соблюдай стиль, используя "Паспорт стиля" и "Описание канала".
        - В конце поста обязательно добавь ссылку на источник.
        ДАННЫЕ:
        - Язык: {channel.get('generation_language') or 'русский'}
        - Описание канала: {channel.get('activity_description')}
        - Паспорт стиля: {channel.get('style_passport')}
        - Ссылка на источник: {best_news.get('link')}
        """
        success, post_text = await generate_text(generation_prompt)
        if not success: return

        image_url = await find_creative_commons_image_url(best_news.get('title')) if scenario['media_strategy'] == 'text_plus_media' else None

        if scenario['posting_mode'] == 'moderation':
            keyboard = get_moderation_keyboard(lang_code, channel_id)
            if image_url:
                await bot.send_photo(chat_id=user_id, photo=image_url, caption=post_text, reply_markup=keyboard)
            else:
                await bot.send_message(chat_id=user_id, text=post_text, reply_markup=keyboard)
            print(f"ПОСТ ОТПРАВЛЕН НА МОДЕРАЦИЮ пользователю {user_id}")
        else:
            if image_url:
                await bot.send_photo(chat_id=channel_id, photo=image_url, caption=post_text)
            else:
                await bot.send_message(chat_id=channel_id, text=post_text)
            print(f"ОПУБЛИКОВАН ПОСТ для канала {channel_id}")
            link_hash = hashlib.sha256(best_news['link'].encode()).hexdigest()
            await db_pool.execute("INSERT INTO published_posts (channel_id, source_url_hash) VALUES ($1, $2)", channel_id, link_hash)

    except Exception as e:
        print(f"Критическая ошибка в scheduled job #{scenario_id}: {e}")
        try:
            await bot.send_message(user_id, get_text(lang_code, 'generic_error_in_job'))
        except Exception as send_e:
            print(f"Не удалось уведомить пользователя {user_id} об ошибке: {send_e}")
    finally:
        await db_pool.close()
        await bot.session.close()
        print(f"--- ЗАДАЧА ДЛЯ СЦЕНАРИЯ #{scenario_id} ЗАВЕРШЕНА ---")

def add_job_to_scheduler(scheduler: AsyncIOScheduler, scenario: dict):
    if not scenario.get('run_times'): return
    
    times = [t.strip() for t in scenario['run_times'].split(',')]
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
    times = [t.strip() for t in scenario['run_times'].split(',')]
    for t in times:
        try:
            hour, minute = map(int, t.split(':'))
            job_id = f"scenario_{scenario['id']}_{hour}_{minute}"
            if scheduler.get_job(job_id):
                scheduler.remove_job(job_id)
                print(f"Задача '{job_id}' удалена из планировщика.")
        except Exception:
            pass

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