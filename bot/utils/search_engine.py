import json
import aiohttp
from bot import config

SERPER_API_KEY = config.SERPER_API_KEY
SERPER_API_URL = "https://google.serper.dev/search"

async def search_news(keywords: list[str], sources: list[str], context_keywords: list[str] = None) -> list[dict]:
    """
    Выполняет один умный поисковый запрос через Serper API с учетом контекста.
    """
    if not SERPER_API_KEY:
        print("КРИТИЧЕСКАЯ ОШИБКА: SERPER_API_KEY не найден. Поиск невозможен.")
        return []

    keyword_query = " OR ".join([f'"{k.strip()}"' for k in keywords])
    
    context_query_part = ""
    if context_keywords:
        context_query = " OR ".join([f'"{k.strip()}"' for k in context_keywords])
        context_query_part = f"AND ({context_query})"

    source_queries = []
    use_google_news = False
    for s in sources:
        s_clean = s.strip()
        if s_clean == 'googlenews':
            use_google_news = True
        elif s_clean == 'twitter':
            source_queries.append('site:x.com')
        else:
            if '.' not in s_clean:
                s_clean += '.com'
            source_queries.append(f'site:{s_clean}')
    
    source_query_part = ""
    if source_queries:
        source_query_part = f"({' OR '.join(source_queries)})"

    full_query = f"({keyword_query}) {context_query_part} {source_query_part}".strip()
    
    payload = {
        "q": full_query,
        "tbs": "qdr:d"
    }
    
    if use_google_news:
        payload['tbm'] = 'nws'

    headers = {'X-API-KEY': SERPER_API_KEY, 'Content-Type': 'application/json'}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(SERPER_API_URL, headers=headers, data=json.dumps(payload)) as response:
                if response.status == 200:
                    data = await response.json()
                    results_key = 'news' if use_google_news else 'organic'
                    results = [
                        {"title": item.get('title'), "link": item.get('link'), "snippet": item.get('snippet')}
                        for item in data.get(results_key, [])
                    ]
                    print(f"Поиск по умному запросу '{full_query}' нашел {len(results)} результатов.")
                    return results
                else:
                    print(f"Ошибка Serper API: Статус {response.status}, Тело ответа: {await response.text()}")
                    return []
    except Exception as e:
        print(f"Исключение при вызове Serper API: {e}")
        return []