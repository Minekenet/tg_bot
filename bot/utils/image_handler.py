import json
import aiohttp
from bot import config

SERPER_API_KEY = config.SERPER_API_KEY
SERPER_IMAGES_URL = "https://google.serper.dev/images"

async def find_creative_commons_image_url(query: str) -> str | None:
    """
    Ищет в Google Images изображение с лицензией Creative Commons.
    """
    if not SERPER_API_KEY:
        print("WARNING: SERPER_API_KEY не найден. Поиск изображений отключен.")
        return None

    headers = {'X-API-KEY': SERPER_API_KEY, 'Content-Type': 'application/json'}
    
    payload = {
        "q": query,
        "tbs": "ic:cl"
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(SERPER_IMAGES_URL, headers=headers, params=payload) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get("images"):
                        image_url = data["images"][0].get("imageUrl")
                        print(f"Найдено изображение с лицензией CC: {image_url}")
                        return image_url
                    else:
                        print(f"Изображения с лицензией CC по запросу '{query}' не найдены.")
                        return None
                else:
                    print(f"Ошибка Serper Images API: Статус {response.status}, Тело ответа: {await response.text()}")
                    return None
    except Exception as e:
        print(f"Исключение при поиске изображения: {e}")
    
    return None