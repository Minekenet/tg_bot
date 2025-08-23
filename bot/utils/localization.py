import json
import os
import re
from html import escape

def escape_html(text: str) -> str:
    """
    Экранирует специальные символы HTML в тексте, удаляя все неподдерживаемые теги Telegram.
    """
    # Список разрешенных тегов Telegram
    # b, strong, i, em, u, ins, s, strike, del, a, code, pre
    allowed_tags = {'b', 'strong', 'i', 'em', 'u', 'ins', 's', 'strike', 'del', 'a', 'code', 'pre'}

    # Экранируем стандартные HTML-символы
    escaped_text = escape(text)

    # Регулярное выражение для поиска всех HTML-тегов
    # Ищет <tag> или </tag>
    tag_pattern = re.compile(r'<(/?)([a-zA-Z][a-zA-Z0-9]*)(?:\s[^>]*)?>')

    def replace_unsupported_tags(match):
        tag_name = match.group(2).lower()
        if tag_name in allowed_tags:
            return match.group(0) # Сохраняем разрешенный тег
        else:
            return '' # Удаляем неподдерживаемый тег

    # Заменяем неподдерживаемые теги пустыми строками
    return tag_pattern.sub(replace_unsupported_tags, escaped_text)

# Загружаем все переводы из папки locales при старте
LOCALES = {}
locales_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'locales')
for lang in os.listdir(locales_dir):
    if lang.endswith('.json'):
        lang_code = lang.split('.')[0]
        with open(os.path.join(locales_dir, lang), 'r', encoding='utf-8') as f:
            LOCALES[lang_code] = json.load(f)

def get_text(lang_code: str, key: str, escape_html_chars: bool = False, **kwargs):
    """
    Получает текст по ключу для заданного языка
    и форматирует его с предоставленными аргументами.
    Если escape_html_chars = True, экранирует специальные символы HTML.
    """
    if not lang_code: # Если язык не определен, используем 'ru' как запасной
        lang_code = 'ru'
    text = LOCALES.get(lang_code, {}).get(key, f"[{key}]")
    try:
        if kwargs:
            text = text.format(**kwargs)
    except KeyError as e:
        print(f"Warning: Placeholder {e} not found in translation for key '{key}'")
    
    if escape_html_chars:
        text = escape_html(text)
        
    return text