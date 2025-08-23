import aiohttp
from readability import Document
from bs4 import BeautifulSoup
import logging
from tenacity import retry, wait_exponential, stop_after_attempt, before_sleep_log
from aiohttp import ClientConnectorError
import tenacity # Добавляем импорт tenacity

logger = logging.getLogger(__name__)

@retry(wait=wait_exponential(multiplier=1, min=4, max=10), stop=stop_after_attempt(5), before_sleep=before_sleep_log(logger, logging.WARNING), reraise=True, retry=(tenacity.retry_if_exception_type(ClientConnectorError)))
async def get_article_text(url: str) -> str | None:
    """
    Получает чистый текст статьи по URL с помощью локальной библиотеки readability.
    Возвращает очищенный текст или None в случае ошибки.
    """
    if not url:
        return None

    # Важно! Притворяемся обычным браузером, чтобы нас не блокировали.
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }

    async with aiohttp.ClientSession() as session:
        # Устанавливаем таймаут, чтобы не ждать вечно "зависшие" сайты
        async with session.get(url, headers=headers, timeout=20) as response:
            if response.status == 200:
                # Получаем сырой HTML страницы
                html_content = await response.text()
                
                # 1. Обрабатываем HTML с помощью readability
                doc = Document(html_content)
                
                # Получаем заголовок и очищенный HTML основной статьи
                title = doc.title()
                clean_html = doc.summary()
                
                logging.debug(f"Readability clean HTML for {url}: {clean_html[:1000]}...") # Логируем первые 1000 символов
                
                # 2. Извлекаем из чистого HTML только текст с помощью BeautifulSoup
                soup = BeautifulSoup(clean_html, 'lxml')
                # .get_text() гениально извлекает весь текст из тегов
                # separator='\n' вставляет переносы строк между блоками для лучшей читаемости
                text_content = soup.get_text(separator='\n', strip=True)
                
                # Соединяем заголовок и текст для полного контекста
                full_article_text = f"Заголовок: {title}\n\n{text_content}"
                
                # Проверяем, достаточно ли текста было извлечено
                MIN_ARTICLE_LENGTH = 200 # Минимальная длина текста статьи для считания ее валидной
                if len(text_content) < MIN_ARTICLE_LENGTH:
                    logging.warning(f"Сценарий: Извлеченный текст для {url} слишком короткий ({len(text_content)} символов), считаем это неудачным скрапингом.")
                    return None
                
                print(f"Успешно извлечен текст статьи с URL: {url}")
                # Обрезаем текст на всякий случай, чтобы не выйти за лимиты токенов модели
                return full_article_text[:15000]
            else:
                print(f"Ошибка при загрузке страницы: Статус {response.status} для URL: {url}")
                return None