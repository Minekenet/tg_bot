# bot/utils/ai_generator.py

import json
import aiohttp
import logging
from bot import config
from bot.utils.localization import get_text # Импортируем здесь, чтобы избежать циклической зависимости

# OpenRouter API settings
OPENROUTER_API_KEY = config.OPENROUTER_API_KEY
OPENROUTER_API_BASE = config.OPENROUTER_API_BASE
OPENROUTER_MODEL = config.OPENROUTER_MODEL
OPENROUTER_SONAR_MODEL = config.OPENROUTER_SONAR_MODEL

MAX_TITLE_CHARS = config.MAX_TITLE_CHARS
MAX_BODY_CHARS = config.MAX_BODY_CHARS
MAX_IMAGE_QUERY_CHARS = config.MAX_IMAGE_QUERY_CHARS
MAX_STYLE_PASSPORT_CHARS = config.MAX_STYLE_PASSPORT_CHARS
MAX_ACTIVITY_DESCRIPTION_CHARS = config.MAX_ACTIVITY_DESCRIPTION_CHARS
MAX_GENERATION_LANGUAGE_CHARS = config.MAX_GENERATION_LANGUAGE_CHARS

async def generate_content_robust(prompt: str) -> tuple[bool, str, int]:
    """
    Универсальная функция для генерации контента с обработкой ошибок через OpenRouter.
    Возвращает статус успеха, сгенерированный текст и количество токенов.
    """
    if not OPENROUTER_API_KEY:
        logging.critical("КРИТИЧЕСКАЯ ОШИБКА: OPENROUTER_API_KEY не найден. Генерация ИИ невозможна.")
        return False, "API ключ OpenRouter не найден.", 0
    
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost:3000", # Можно заменить на реальный URL вашего приложения
    }

    # OpenRouter использует формат OpenAI Chat Completions
    payload = {
        "model": OPENROUTER_MODEL,
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.7, # можно настроить
        "max_tokens": 800
    }

    logging.info(f"Отправка запроса к OpenRouter API. Модель: {OPENROUTER_MODEL}")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{OPENROUTER_API_BASE}/chat/completions", headers=headers, json=payload, timeout=60) as response:
                if response.status == 200:
                    data = await response.json()
                    # Проверяем наличие текста в ответе
                    if not data.get("choices") or not data["choices"][0].get("message") or not data["choices"][0]["message"].get("content"):
                        logging.error(f"OpenRouter вернул пустой или некорректный ответ: {data}")
                        return False, "OpenRouter вернул пустой или некорректный ответ.", 0
                    
                    generated_text = data["choices"][0]["message"]["content"]
                    token_count = data.get("usage", {}).get("total_tokens", 0)
                    logging.info(f"Успешная генерация контента от OpenRouter. Токенов использовано: {token_count}")
                    return True, generated_text, token_count
                else:
                    error_text = await response.text()
                    logging.error(f"Ошибка OpenRouter API: Статус {response.status}, Тело ответа: {error_text}")
                    return False, f"Ошибка OpenRouter API: {error_text}", 0
    except aiohttp.ClientError as e:
        error_message = f"Ошибка сетевого запроса к OpenRouter: {e}"
        logging.critical(error_message, exc_info=True)
        return False, error_message, 0
    except Exception as e:
        error_message = f"Произошла непредвиденная ошибка при генерации контента через OpenRouter: {e}"
        logging.critical(error_message, exc_info=True)
        return False, error_message, 0

def is_article_url(url: str) -> bool:
    """
    Проверяет, является ли URL ссылкой на конкретную статью (а не на главную страницу).
    Простая эвристика: URL должен содержать как минимум один сегмент пути после домена
    или иметь явное расширение файла.
    """
    # Разбираем URL
    from urllib.parse import urlparse
    parsed_url = urlparse(url)

    # Если нет пути или путь это просто один слэш, считаем главной страницей
    if not parsed_url.path or parsed_url.path == '/':
        return False

    # Если путь имеет несколько сегментов (например, /category/article)
    # или если есть расширение файла (например, .html, .php)
    # Эту логику можно уточнить при необходимости
    path_segments = [segment for segment in parsed_url.path.split('/') if segment]
    if len(path_segments) > 0 and '.' in path_segments[-1]: # Предполагаем, что есть файл
        return True
    if len(path_segments) > 1: # Более одного сегмента пути (e.g., /category/article)
        return True

    # Исключаем URL, которые выглядят как главные страницы, но с добавлением языка, например, /ru/
    # Это уже покрывается `len(path_segments) > 0` и `parsed_url.path == '/'`

    return False

async def generate_style_passport_from_text(posts_text: str, lang_code: str) -> tuple[bool, str, int]:
    """
    Генерирует "Паспорт стиля" из предоставленного текста постов на указанном языке.
    """
    from bot.config import MAX_CHARS_FOR_PASSPORT

    prompt = get_text(lang_code, "style_passport_ai_prompt", 
                       MAX_CHARS_FOR_PASSPORT=MAX_CHARS_FOR_PASSPORT, 
                       posts_text=posts_text)
    
    success, passport_text, token_count = await generate_content_robust(prompt)
    
    # Логирование в usage_ledger для учета стоимости паспорта стиля (дешевая модель)
    # Замечание: тут нет db_pool в контексте, поэтому логирование делается в местах вызова,
    # где db_pool доступен (например, в хендлерах). Эта функция возвращает токены.
    return success, passport_text, token_count

async def select_best_articles_from_search_results(articles: list[dict], lang_code: str) -> tuple[bool, list[str], int]:
    """
    Использует ИИ для выбора 3 лучших статей из списка результатов поиска.
    Возвращает статус успеха, список URL выбранных статей и количество токенов.
    """
    if not articles:
        return True, [], 0

    # Фильтруем входные статьи, оставляя только те, что ведут на конкретные статьи
    filtered_input_articles = [article for article in articles if is_article_url(article.get('url', ''))]

    if not filtered_input_articles:
        logging.warning("После фильтрации корневых ссылок не осталось статей для выбора ИИ.")
        return True, [], 0

    formatted_articles = []
    for i, article in enumerate(filtered_input_articles):
        formatted_articles.append(
            f"Article {i+1}:\n"
            f"URL: {article.get('url')}\n"
            f"Title: {article.get('title')}\n"
            f"Snippet: {article.get('passages')}\n"
        )
    
    articles_list_str = "\n---\n".join(formatted_articles)
    
    prompt = get_text(lang_code, "article_selection_ai_prompt", articles_list=articles_list_str)
    
    success, raw_response, token_count = await generate_content_robust(prompt)
    
    if success:
        try:
            selected_urls = json.loads(raw_response)
            # Убедимся, что это список строк и не более 3-х элементов
            if isinstance(selected_urls, list) and all(isinstance(url, str) for url in selected_urls):
                # Удаляем пост-фильтрацию, так как входные статьи уже отфильтрованы.
                # filtered_urls = [url for url in selected_urls if is_article_url(url)]
                return True, selected_urls[:3], token_count
            else:
                logging.error(f"ИИ вернул некорректный формат для выбора статей: {raw_response}")
                return False, [], token_count
        except json.JSONDecodeError as e:
            logging.error(f"Ошибка парсинга JSON от ИИ при выборе статей: {e}. Ответ: {raw_response}")
            return False, [], token_count
    else:
        return False, [], token_count


async def generate_post_via_sonar(theme: str, keywords: list[str], lang_code: str,
                                  style_passport: str = "",
                                  activity_description: str = "",
                                  generation_language: str = "") -> tuple[bool, dict, int]:
    """
    Использует Perplexity Sonar через OpenRouter для поиска свежей новости (<=12 часов)
    по теме и тегам, и генерирует готовый пост. Возвращает (success, data, tokens),
    где data = { title, body, image_query, source_url } с ограничениями по длине.
    """
    if not OPENROUTER_API_KEY:
        logging.critical("КРИТИЧЕСКАЯ ОШИБКА: OPENROUTER_API_KEY не найден. Генерация ИИ невозможна.")
        return False, {"error": "API key missing"}, 0

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost:3000",
    }

    # Ограничим длину входных данных на всякий случай
    safe_theme = (theme or "").strip()[:200]
    safe_keywords = [k.strip()[:100] for k in (keywords or [])][:10]
    safe_passport = (style_passport or "").strip()[:MAX_STYLE_PASSPORT_CHARS]
    safe_activity = (activity_description or "").strip()[:MAX_ACTIVITY_DESCRIPTION_CHARS]
    safe_generation_lang = (generation_language or lang_code or "ru").strip()[:MAX_GENERATION_LANGUAGE_CHARS]

    # Промпт с жесткими требованиями по свежести и формату
    locale = "ru" if (safe_generation_lang or "ru").startswith("ru") else "en"
    instructions = (
        f"Ты помощник-редактор. Найди в интернете ОДНУ актуальную новость, опубликованную не ранее чем 12 часов назад, "
        f"по теме и ключевым словам. Верни строго JSON без пояснений. Язык ответа: {locale}.\n\n"
        f"Тема: {safe_theme}\n"
        f"Ключевые слова: {', '.join(safe_keywords)}\n"
        f"Паспорт стиля: {safe_passport}\n"
        f"Описание канала/деятельности: {safe_activity}\n"
        f"Язык генерации: {safe_generation_lang}\n\n"
        f"Требования:\n"
        f"- Источник должен быть текстовая статья, не видео.\n"
        f"- Дата публикации должна быть в пределах последних 12 часов. Игнорируй результаты старше.\n"
        f"- Проверь реальность источника (известные СМИ/блоги).\n"
        f"- Сгенерируй структурированный пост. Используй только теги Telegram HTML: <b>, <i>, <u>, <s>, <a>, <code>, <pre>.\n"
        f"- Соблюдай лимиты символов.\n\n"
        f"Формат JSON строго такой:\n"
        f"{{\n"
        f"  \"title\": string (<= {MAX_TITLE_CHARS} chars),\n"
        f"  \"body\": string (<= {MAX_BODY_CHARS} chars),\n"
        f"  \"image_query\": string (<= {MAX_IMAGE_QUERY_CHARS} chars),\n"
        f"  \"source_url\": string (valid URL to the article)\n"
        f"}}\n\n"
        f"Правила оформления:\n"
        f"- title — короткий, цепляющий, без эмодзи.\n"
        f"- body — информативно и лаконично, без воды и клише; допускаются только указанные теги Telegram HTML.\n"
        f"- image_query — краткий запрос для поиска иллюстрации по теме новости.\n"
        f"- Не выходи за лимиты символов. Если нужно — укорачивай.\n"
        f"- Верни ТОЛЬКО JSON."
    )

    payload = {
        "model": OPENROUTER_SONAR_MODEL,
        "messages": [
            {"role": "system", "content": "You are Perplexity Sonar web search assistant."},
            {"role": "user", "content": instructions}
        ],
        "temperature": 0.2,
        "max_tokens": 800
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{OPENROUTER_API_BASE}/chat/completions", headers=headers, json=payload, timeout=60) as response:
                if response.status == 200:
                    data = await response.json()
                    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    token_count = data.get("usage", {}).get("total_tokens", 0)
                    # Попытка распарсить JSON из ответа
                    try:
                        parsed = json.loads(content)
                        # Принудительно режем по лимитам
                        parsed["title"] = (parsed.get("title") or "")[:MAX_TITLE_CHARS]
                        parsed["body"] = (parsed.get("body") or "")[:MAX_BODY_CHARS]
                        parsed["image_query"] = (parsed.get("image_query") or "")[:MAX_IMAGE_QUERY_CHARS]
                        return True, parsed, token_count
                    except Exception:
                        logging.error(f"Sonar вернул не-JSON: {content}")
                        return False, {"error": "Non-JSON from Sonar"}, token_count
                else:
                    error_text = await response.text()
                    logging.error(f"Ошибка Sonar(OpenRouter) API: Статус {response.status}, Тело: {error_text}")
                    return False, {"error": error_text}, 0
    except aiohttp.ClientError as e:
        logging.critical(f"Сетевая ошибка Sonar(OpenRouter): {e}", exc_info=True)
        return False, {"error": str(e)}, 0
    except Exception as e:
        logging.critical(f"Исключение при генерации через Sonar: {e}", exc_info=True)
        return False, {"error": str(e)}, 0