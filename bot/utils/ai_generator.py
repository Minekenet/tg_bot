import os
from dotenv import load_dotenv
import google.generativeai as genai

# Загружаем переменные окружения (включая наш ключ)
load_dotenv()

# --- Инициализация API с помощью простого ключа ---
try:
    AI_STUDIO_API_KEY = os.getenv("AI_STUDIO_API_KEY")
    
    if not AI_STUDIO_API_KEY:
        raise ValueError("AI_STUDIO_API_KEY не найден в .env файле.")

    # Конфигурируем библиотеку с нашим ключом
    genai.configure(api_key=AI_STUDIO_API_KEY)
    
    # Создаем экземпляр модели. Мы будем использовать самую дешевую и быструю.
    # 'gemini-1.5-flash-latest' - это правильное название для API.
    gemini_flash_model = genai.GenerativeModel('gemini-1.5-flash-latest')
    
    print("✅ Инициализация с API-ключом Google AI Studio прошла успешно.")

except Exception as e:
    print(f"КРИТИЧЕСКАЯ ОШИБКА: Не удалось инициализировать модель Gemini. Ошибка: {e}")
    gemini_flash_model = None


async def generate_style_passport_from_text(posts_text: str) -> tuple[bool, str]:
    """
    Генерирует "Паспорт стиля" из предоставленного текста постов.
    НЕ ИСПОЛЬЗУЕТ ПОИСК В ИНТЕРНЕТЕ.
    
    :param posts_text: Единая строка, содержащая все посты пользователя.
    :return: Кортеж (успех: bool, результат_или_ошибка: str).
    """
    if not gemini_flash_model:
        return False, "Модель Gemini не была инициализирована. Проверьте логи сервера."

    # Создаем специальный промпт для задачи анализа и структурирования
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
        # Отправляем асинхронный запрос к модели
        response = await gemini_flash_model.generate_content_async(prompt)
        
        # Возвращаем сгенерированный текст
        generated_text = response.text
        return True, generated_text

    except Exception as e:
        error_message = f"Произошла ошибка при генерации паспорта стиля: {e}"
        print(error_message)
        return False, error_message