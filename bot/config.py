import os
from dotenv import load_dotenv

# Загружаем переменные из .env файла для локальной разработки
# Это нужно, чтобы бот работал и без Docker, например, при локальном дебаге
load_dotenv()

def get_secret(secret_name: str, default: str = None) -> str:
    """
    Получает секрет. Сначала пытается прочитать его из Docker Secrets,
    если не получается - из переменных окружения (которые загрузились из .env).
    Это позволяет коду работать как в продакшене, так и при локальном запуске.
    """
    secret_name_upper = secret_name.upper()
    secret_path = f"/run/secrets/{secret_name}"
    try:
        with open(secret_path, 'r', encoding='utf-8') as secret_file:
            return secret_file.read().strip()
    except IOError:
        # Если файл не найден (мы не в Docker с secrets),
        # пытаемся получить из переменных окружения
        return os.getenv(secret_name_upper, default)

# --- Загружаем все наши секреты ---
BOT_TOKEN = get_secret("bot_token")
ADMIN_USER_IDS = get_secret("admin_user_ids", "")
XMLRIVER_API_KEY = get_secret("xmlriver_api_key")
XMLRIVER_USER_ID = get_secret("xmlriver_user_id", "18601") # Добавляем user_id для XMLRiver

OPENROUTER_API_KEY = get_secret("openrouter_api_key")
OPENROUTER_API_BASE = "https://openrouter.ai/api/v1"
OPENROUTER_MODEL = "meta-llama/llama-3.2-3b-instruct"
OPENROUTER_SONAR_MODEL = os.getenv("OPENROUTER_SONAR_MODEL", "perplexity/sonar")

DB_USER = get_secret("db_user")
DB_PASSWORD = get_secret("db_password")
DB_NAME = get_secret("db_name")
# Хост БД обычно не секрет, но для консистентности можно тоже сделать секретом
# или оставить в .env. Для продакшена с Docker это всегда имя сервиса.
DB_HOST = os.getenv("DB_HOST", "db")

# --- Преобразуем список админов в нужный формат ---
ADMINS = [int(admin_id.strip()) for admin_id in ADMIN_USER_IDS.split(',') if admin_id.strip()]

# --- Константы стоимости --- 
# XMLRiver (картинки) — стоимость одного запроса в рублях
SEARCH_QUERY_COST = float(os.getenv("SEARCH_QUERY_COST", "0.02"))

# Sonar (OpenRouter) — стоимость одного запроса в рублях (по умолчанию 0.5 RUB ≈ $0.005 при курсе ~100)
SONAR_REQUEST_COST_RUB = float(os.getenv("SONAR_REQUEST_COST_RUB", "0.5"))

# Стоимость токенов моделей через OpenRouter
# 1 000 000 токенов = 120 рублей => 0.12 руб за 1000 токенов
AI_TOKEN_COST_PER_1M_RUB = float(os.getenv("AI_TOKEN_COST_PER_1M_RUB", "120"))
AI_TOKEN_COST_PER_1000 = AI_TOKEN_COST_PER_1M_RUB / 1000.0
MAX_CHARS_FOR_PASSPORT = 3000 # Максимальное количество символов для "Паспорта стиля" AI
MAX_TITLE_CHARS = int(os.getenv("MAX_TITLE_CHARS", "120"))
MAX_BODY_CHARS = int(os.getenv("MAX_BODY_CHARS", "2000"))
MAX_IMAGE_QUERY_CHARS = int(os.getenv("MAX_IMAGE_QUERY_CHARS", "120"))
MAX_STYLE_PASSPORT_CHARS = int(os.getenv("MAX_STYLE_PASSPORT_CHARS", "2000"))
MAX_ACTIVITY_DESCRIPTION_CHARS = int(os.getenv("MAX_ACTIVITY_DESCRIPTION_CHARS", "500"))
MAX_GENERATION_LANGUAGE_CHARS = int(os.getenv("MAX_GENERATION_LANGUAGE_CHARS", "50"))

# Минимальный интервал между запусками сценария в минутах
MIN_SCENARIO_INTERVAL_MINUTES = int(os.getenv("MIN_SCENARIO_INTERVAL_MINUTES", "15"))

XMLRIVER_API_KEY = get_secret("xmlriver_api_key")
XMLRIVER_NEWS_URL = "http://xmlriver.com/search/xml"

# --- Проверка наличия ключевых токенов ---
if not BOT_TOKEN:
    raise ValueError("Необходимо указать BOT_TOKEN в секретах или .env")
if not ADMINS:
    print("ВНИМАНИЕ: Не указаны ADMIN_USER_IDS. Функции админки и поддержки работать не будут.")