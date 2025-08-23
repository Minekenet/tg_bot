import json
import aiohttp
import logging
import xml.etree.ElementTree as ET # Import for XML parsing
from bot import config

XMLRIVER_API_KEY = config.XMLRIVER_API_KEY
XMLRIVER_USER_ID = config.XMLRIVER_USER_ID # Получаем USER_ID из конфига
XMLRIVER_NEWS_URL = config.XMLRIVER_NEWS_URL

async def search_news(theme: str, keywords: list[str], user_lang_code: str) -> tuple[str, int]:
    """
    Выполняет поисковый запрос через xmlriver.com, который возвращает данные в XML формате.
    Использует Тему и Ключевые слова сценария.
    Возвращает список результатов и количество выполненных запросов (0 или 1).
    """
    if not XMLRIVER_API_KEY:
        logging.critical("КРИТИЧЕСКАЯ ОШИБКА: XMLRIVER_API_KEY не найден. Поиск невозможен.")
        return "", 0

    search_terms = [theme.strip().lower()] + [k.strip().lower() for k in keywords]
    unique_terms = list(set([term for term in search_terms if term]))
    
    if not unique_terms:
        logging.warning("Попытка поиска с пустой темой и ключевыми словами.")
        return "", 0
        
    query = " ".join(unique_terms)

    # Определение страны и языка для xmlriver.com
    # 'lr' (language region) - код языка из файла языков
    # 'country' - числовое значение (id) страны
    # 'loc' - числовое значение (id) местоположения (если нужно)
    # 'domain' - числовое значение (id) google домена
    # 'device' - устройство (desktop, tablet, mobile)

    # Приводим user_lang_code к формату 'ru' или 'en'
    # TODO: Возможно, потребуется более сложная логика для определения 'loc', 'country', 'domain'
    # на основе user_lang_code или других параметров. Пока используем базовые значения.
    if user_lang_code == 'ru':
        lr_code = '225'  # Россия для lr параметра
        # country_id = '2643' # ID России (пример из OCR)
        # domain_id = '108' # ID google.ru (пример из OCR)
    else:
        lr_code = '93'  # США для lr параметра
        # country_id = '2008' # ID США (пример)
        # domain_id = '1' # ID google.com (пример)

    params = {
        "setab": "news", # Возвращаем параметр для новостного поиска
        "key": XMLRIVER_API_KEY,
        "user": XMLRIVER_USER_ID, # Добавляем user_id
        "query": query,
        "lr": lr_code,
        "tbs": "qdr:d", # Добавляем фильтр за последние 24 часа
        "groupby": "10" # TOP 10 результатов
    }

    logging.info(f"Выполняю поиск в XMLRiver. Запрос: '{query}', Язык региона: {lr_code}, User ID: {XMLRIVER_USER_ID}")

    try:
        async with aiohttp.ClientSession() as session:
            # XMLRiver использует GET-запросы
            async with session.get(XMLRIVER_NEWS_URL, params=params, timeout=20) as response:
                if response.status == 200:
                    xml_text = await response.text()
                    
                    logging.info(f"Поиск XMLRiver выполнен. Получен XML-ответ.")
                    return xml_text, 1
                else:
                    error_text = await response.text()
                    logging.error(f"Ошибка XMLRiver API: Статус {response.status}, Тело ответа: {error_text}")
                    return "", 0
    except Exception as e:
        logging.critical(f"Исключение при вызове XMLRiver API: {e}", exc_info=True)
        return "", 0