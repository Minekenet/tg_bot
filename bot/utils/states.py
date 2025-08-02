from aiogram.fsm.state import State, StatesGroup

class FolderCreation(StatesGroup):
    waiting_for_name = State()

# Состояние для создания паспорта стиля конкретного канала
class ChannelStylePassportCreation(StatesGroup):
    collecting_posts = State()