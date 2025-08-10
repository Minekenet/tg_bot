from aiogram.fsm.state import State, StatesGroup

class FolderCreation(StatesGroup):
    waiting_for_name = State()

class ChannelStylePassportCreation(StatesGroup):
    collecting_posts = State()

class ChannelDescription(StatesGroup):
    waiting_for_description = State()

class ScenarioCreation(StatesGroup):
    waiting_for_name = State()
    adding_keywords = State()
    adding_times = State()
    choosing_sources = State()
    choosing_media_strategy = State()
    choosing_posting_mode = State()
    waiting_for_timezone = State()

# ИЗМЕНЕНО: Добавлены новые состояния для редактирования
class ScenarioEditing(StatesGroup):
    choosing_option = State()
    editing_name = State()
    editing_keywords = State()
    editing_sources = State()
    editing_media_strategy = State()
    editing_posting_mode = State()
    editing_times = State()
    editing_timezone = State()

class BroadcastState(StatesGroup):
    waiting_for_message = State()
    confirming_message = State()

class ChannelLanguage(StatesGroup):
    waiting_for_language = State()

class SupportRequest(StatesGroup):
    waiting_for_message = State()
    waiting_for_reply_from_admin = State()

class Onboarding(StatesGroup):
    waiting_for_channel = State()
    waiting_for_passport = State()
    waiting_for_description = State()
    waiting_for_language = State()

class AddChannel(StatesGroup):
    waiting_for_input = State()

class DirectMessage(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_message = State()
    confirming_message = State()

class PromoCodeCreation(StatesGroup):
    waiting_for_name = State()
    waiting_for_generations = State()
    waiting_for_uses = State()

class PromoCodeActivation(StatesGroup):
    waiting_for_code = State()