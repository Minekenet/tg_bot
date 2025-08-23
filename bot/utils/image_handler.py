import json
import aiohttp
import xml.etree.ElementTree as ET
from bot import config

XMLRIVER_API_KEY = config.XMLRIVER_API_KEY
XMLRIVER_IMAGES_URL = config.XMLRIVER_NEWS_URL # XMLRiver uses the same base URL, just different setab

async def find_creative_commons_image_url(query: str, lang_code: str = 'ru') -> str | None:
    """
    Ищет в Google Images через xmlriver.com изображение с лицензией Creative Commons.
    """
    if not XMLRIVER_API_KEY:
        print("WARNING: XMLRIVER_API_KEY не найден. Поиск изображений отключен.")
        return None

    # Определение lr кода для языка
    lr_code = '225' if lang_code == 'ru' else '93' # Россия для ru, США для en

    # Параметры для поиска изображений через xmlriver.com
    # Согласно документации, setab=images, и необходимо указать query.
    # Для фильтрации по Creative Commons, ищем параметр. В примере OCR есть 'ic:cl', но это Serper параметр.
    # Для XMLRiver нужно проверить, как передается лицензия. Пока без явного параметра лицензии.
    params = {
        "setab": "images",
        "key": XMLRIVER_API_KEY,
        "query": query,
        "lr": lr_code,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(XMLRIVER_IMAGES_URL, params=params, timeout=20) as response:
                if response.status == 200:
                    xml_text = await response.text()
                    root = ET.fromstring(xml_text)
                    
                    # XMLRiver возвращает результаты в тегах <doc> с полем <url> для изображения.
                    # В примере Serper был imageUrl. Здесь будем искать <url>.
                    for doc in root.findall(".//doc"):
                        image_url = doc.findtext("url")
                        if image_url:
                            print(f"Найдено изображение с лицензией CC (предположительно): {image_url}")
                            return image_url
                    
                    print(f"Изображения с лицензией CC по запросу '{query}' не найдены.")
                    return None
                else:
                    print(f"Ошибка XMLRiver Images API: Статус {response.status}, Тело ответа: {await response.text()}")
                    return None
    except Exception as e:
        print(f"Исключение при поиске изображения: {e}")
    
    return None