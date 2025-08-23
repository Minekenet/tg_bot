# Используем официальный образ Python
FROM python:3.10-slim

# Устанавливаем рабочую директорию внутри контейнера
WORKDIR /app

# Копируем файл с зависимостями и устанавливаем их
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN pip install aiogram==3.1.1 SQLAlchemy==2.0.23 aiohttp==3.8.5 redis==5.0.1 APScheduler==3.10.4 psycopg2-binary==2.9.9 beautifulsoup4==4.12.2 python-dotenv==1.0.0 python-magic==0.4.27 tenacity==8.2.3

# Копируем все остальные файлы проекта в рабочую директорию
COPY . .

# Команда, которая будет запущена при старте контейнера
CMD ["python", "-m", "bot.bot_main"]