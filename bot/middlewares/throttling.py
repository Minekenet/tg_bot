import asyncio
from typing import Callable, Dict, Any, Awaitable

from aiogram import BaseMiddleware
from aiogram.types import Message

# Это будет наш "кеш" для хранения времени последнего сообщения от пользователя
# В реальном продакшене для ботов с огромной аудиторией лучше использовать Redis,
# но для старта и десятков тысяч пользователей словаря в памяти будет достаточно.
cache = {}

class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, time_limit: float = 0.7):
        """
        :param time_limit: Задержка в секундах. 0.7 секунды - оптимальное значение,
                         чтобы не раздражать пользователя и эффективно бороться с флудом.
        """
        self.time_limit = time_limit

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any]
    ) -> Any:
        # Проверяем, что событие - это сообщение от реального пользователя
        if not hasattr(event, 'from_user') or event.from_user is None:
            return await handler(event, data)
            
        user_id = event.from_user.id

        # Проверяем, есть ли пользователь в кеше
        if user_id in cache:
            # Считаем, сколько времени прошло с последнего сообщения
            time_diff = asyncio.get_event_loop().time() - cache[user_id]
            if time_diff < self.time_limit:
                # Если времени прошло слишком мало, просто игнорируем это обновление
                return
        
        # Обновляем время последнего сообщения в кеше
        cache[user_id] = asyncio.get_event_loop().time()
        
        # Передаем управление дальше, следующему обработчику
        return await handler(event, data)