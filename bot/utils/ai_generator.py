# bot/utils/ai_generator.py

import google.generativeai as genai
from bot import config
import logging

try:
    AI_STUDIO_API_KEY = config.AI_STUDIO_API_KEY
    
    if not AI_STUDIO_API_KEY:
        raise ValueError("AI_STUDIO_API_KEY не найден в секретах или .env.")

    genai.configure(api_key=AI_STUDIO_API_KEY)
    
    gemini_flash_model = genai.GenerativeModel('gemini-1.5-flash-latest')
    
    logging.info("✅ Инициализация с API-ключом Google AI Studio прошла успешно.")

except Exception as e:
    logging.critical(f"КРИТИЧЕСКАЯ ОШИБКА: Не удалось инициализировать модель Gemini. Ошибка: {e}", exc_info=True)
    gemini_flash_model = None

async def generate_content_robust(prompt: str) -> tuple[bool, str]:
    """
    Универсальная функция для генерации контента с обработкой ошибок.
    """
    if not gemini_flash_model:
        return False, "Модель Gemini не была инициализирована. Проверьте логи сервера."

    try:
        # Устанавливаем разумный таймаут
        response = await gemini_flash_model.generate_content_async(prompt, request_options={'timeout': 60})
        
        # Проверяем наличие текста в ответе
        if not response.text:
            return False, "Gemini вернул пустой ответ."
        
        generated_text = response.text
        return True, generated_text
    
    except genai.types.BlockedPromptException as e:
        error_message = f"Запрос к ИИ заблокирован: {e}"
        logging.error(error_message, exc_info=True)
        return False, error_message
    except Exception as e:
        error_message = f"Произошла ошибка при генерации контента: {e}"
        logging.error(error_message, exc_info=True)
        return False, error_message

async def generate_style_passport_from_text(posts_text: str) -> tuple[bool, str]:
    """
    Генерирует "Паспорт стиля" из предоставленного текста постов.
    Использует новую функцию generate_content_robust
    """
    prompt = f"""
    Твоя задача - выступить в роли опытного контент-аналитика. Проанализируй следующие посты из Telegram-канала. На основе их стиля, тона и содержания, создай детальный, но краткий "Паспорт стиля" в формате Markdown.

    Паспорт должен включать следующие разделы:
    - **Tone of Voice (Тон голоса):** (например: "Экспертный, но дружелюбный и с юмором", "Строго-формальный, деловой", "Провокационный и молодежный").
    - **Ключевые темы:** (Перечисли основные темы, о которых пишет автор).
    - **Структура и формат постов:** (Опиши типичную структуру: есть ли заголовки, используются ли списки, эмодзи, какая средняя длина постов).
    - **Целевая аудитория:** (Опиши, для кого, скорее всего, предназначены эти посты).
    - **Примеры удачных фраз или оборотов:** (Приведи 2-3 цитаты из текста, которые хорошо отражают стиль).

    Вот посты для анализа:
    ---
    {posts_text}
    ---

    Создай "Паспорт стиля". Ответ должен быть только в формате Markdown.
    """

    return await generate_content_robust(prompt)