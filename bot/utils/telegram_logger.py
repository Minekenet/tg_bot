import logging
import asyncio
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties

class TelegramLogsHandler(logging.Handler):
    def __init__(self, bot_token: str, chat_id: int):
        super().__init__()
        self.chat_id = chat_id
        self.bot = Bot(token=bot_token, default=DefaultBotProperties(parse_mode="HTML"))

    def emit(self, record: logging.LogRecord):
        log_entry = self.format(record)
        try:
            # Проверяем, запущен ли цикл событий asyncio
            loop = asyncio.get_running_loop()
            if loop.is_running():
                # Если да, то безопасно создаем задачу
                loop.create_task(self.bot.send_message(self.chat_id, log_entry))
            else:
                # Если нет (например, при падении на старте), отправляем синхронно
                # (это блокирующая операция, но на этапе падения это не страшно)
                asyncio.run(self.bot.send_message(self.chat_id, log_entry))
        except RuntimeError:
             # Если get_running_loop падает, потому что цикла нет вообще
             asyncio.run(self.bot.send_message(self.chat_id, log_entry))
        except Exception as e:
            print(f"CRITICAL: Could not send log message to Telegram: {e}")