import json
import os

# Загружаем все переводы из папки locales при старте
LOCALES = {}
locales_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'locales')
for lang in os.listdir(locales_dir):
    if lang.endswith('.json'):
        lang_code = lang.split('.')[0]
        with open(os.path.join(locales_dir, lang), 'r', encoding='utf-8') as f:
            LOCALES[lang_code] = json.load(f)

def get_text(lang_code: str, key: str, **kwargs):
    """
    Получает текст по ключу для заданного языка
    и форматирует его с предоставленными аргументами.
    """
    if not lang_code: # Если язык не определен, используем 'ru' как запасной
        lang_code = 'ru'
    text = LOCALES.get(lang_code, {}).get(key, f"[{key}]")
    try:
        if kwargs:
            text = text.format(**kwargs)
    except KeyError as e:
        print(f"Warning: Placeholder {e} not found in translation for key '{key}'")
    return text