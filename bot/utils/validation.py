import html
import re

# Максимальная длина для разных типов текста
MAX_NAME_LENGTH = 80
MAX_DESCRIPTION_LENGTH = 2000
MAX_KEYWORD_LENGTH = 50

# Регулярное выражение для проверки имен (папок, сценариев)
# Разрешает буквы (включая кириллицу), цифры, пробелы и символы -_.
VALID_NAME_PATTERN = re.compile(r"^[A-Za-zА-Яа-я0-9\s\-_.]+$")

def sanitize_text(text: str | None) -> str:
    """
    Очищает текст от потенциально опасных HTML-тегов.
    Возвращает пустую строку, если на входе None.
    """
    if not text:
        return ""
    return html.escape(text)

def is_valid_name(name: str) -> bool:
    """
    Проверяет, соответствует ли имя заданным правилам:
    - Не пустое
    - Не превышает максимальную длину
    - Содержит только разрешенные символы
    """
    if not name or len(name) > MAX_NAME_LENGTH:
        return False
    if not VALID_NAME_PATTERN.match(name):
        return False
    return True

def is_valid_keyword(keyword: str) -> bool:
    """
    Проверяет валидность ключевого слова.
    """
    if not keyword or len(keyword) > MAX_KEYWORD_LENGTH:
        return False
    # Для ключевых слов можно разрешить более широкий набор символов,
    # но все равно лучше избегать некоторых спецсимволов.
    # В данном случае просто проверяем длину и непустоту.
    return True

def is_valid_description(description: str) -> bool:
    """
    Проверяет валидность описания канала.
    """
    return 0 < len(description) <= MAX_DESCRIPTION_LENGTH