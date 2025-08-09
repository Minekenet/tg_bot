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
SERPER_API_KEY = get_secret("serper_api_key")
AI_STUDIO_API_KEY = get_secret("ai_studio_api_key")

DB_USER = get_secret("db_user")
DB_PASSWORD = get_secret("db_password")
DB_NAME = get_secret("db_name")
# Хост БД обычно не секрет, но для консистентности можно тоже сделать секретом
# или оставить в .env. Для продакшена с Docker это всегда имя сервиса.
DB_HOST = os.getenv("DB_HOST", "db")

# --- Преобразуем список админов в нужный формат ---
ADMINS = [int(admin_id.strip()) for admin_id in ADMIN_USER_IDS.split(',') if admin_id.strip()]

# --- Проверка наличия ключевых токенов ---
if not BOT_TOKEN:
    raise ValueError("Необходимо указать BOT_TOKEN в секретах или .env")
if not ADMINS:
    print("ВНИМАНИЕ: Не указаны ADMIN_USER_IDS. Функции админки и поддержки работать не будут.")