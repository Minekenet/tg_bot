import google.generativeai as genai
from bot import config

try:
    AI_STUDIO_API_KEY = config.AI_STUDIO_API_KEY
    
    if not AI_STUDIO_API_KEY:
        raise ValueError("AI_STUDIO_API_KEY не найден в секретах или .env.")

    genai.configure(api_key=AI_STUDIO_API_KEY)
    
    gemini_flash_model = genai.GenerativeModel('gemini-1.5-flash-latest')
    
    print("✅ Инициализация с API-ключом Google AI Studio прошла успешно.")

except Exception as e:
    print(f"КРИТИЧЕСКАЯ ОШИБКА: Не удалось инициализировать модель Gemini. Ошибка: {e}")
    gemini_flash_model = None

async def generate_style_passport_from_text(posts_text: str) -> tuple[bool, str]:
    """
    Генерирует "Паспорт стиля" из предоставленного текста постов.
    """
    if not gemini_flash_model:
        return False, "Модель Gemini не была инициализирована. Проверьте логи сервера."

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

    try:
        response = await gemini_flash_model.generate_content_async(prompt)
        generated_text = response.text
        return True, generated_text
    except Exception as e:
        error_message = f"Произошла ошибка при генерации паспорта стиля: {e}"
        print(error_message)
        return False, error_message