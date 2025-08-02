# Используем официальный образ Python
FROM python:3.10-slim

# Устанавливаем рабочую директорию внутри контейнера
WORKDIR /app

# Копируем файл с зависимостями и устанавливаем их
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем все остальные файлы проекта в рабочую директорию
COPY . .

# Команда, которая будет запущена при старте контейнера
CMD ["python", "-m", "bot.bot_main"]