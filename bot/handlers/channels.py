import datetime
import re
import logging
import asyncpg
from aiogram import Router, F, Bot, types
from aiogram.filters import StateFilter
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.utils.states import FolderCreation, ChannelStylePassportCreation, ChannelDescription, ChannelLanguage, AddChannel, Onboarding
from bot.utils.localization import get_text
from bot.utils.validation import sanitize_text, is_valid_name, is_valid_description
from bot.keyboards.inline import (
    get_channels_keyboard, get_folder_view_keyboard, get_channel_manage_keyboard,
    get_channel_move_keyboard, get_confirmation_keyboard, get_style_passport_creation_keyboard,
    get_onboarding_after_channel_keyboard, get_cancel_add_channel_keyboard
)
from bot.utils.ai_generator import generate_style_passport_from_text

router = Router()

# --- Константы и вспомогательные функции ---
MAX_POSTS_FOR_PASSPORT = 10
MAX_CHARS_FOR_PASSPORT = 10000
MAX_CHARS_FOR_DESCRIPTION = 2000
PASSPORT_UPDATE_COOLDOWN = datetime.timedelta(days=3)

async def get_user_language(user_id: int, db_pool: asyncpg.Pool) -> str:
    if db_pool:
        async with db_pool.acquire() as connection:
            return await connection.fetchval("SELECT language_code FROM users WHERE user_id = $1", user_id) or 'ru'
    return 'ru'

async def show_channels_menu(message: Message | CallbackQuery, db_pool: asyncpg.Pool, page: int = 0):
    user_id = message.from_user.id
    lang_code = await get_user_language(user_id, db_pool)
    keyboard = await get_channels_keyboard(user_id, lang_code, db_pool, page)
    text = get_text(lang_code, 'your_channels_title')
    try:
        if isinstance(message, CallbackQuery):
            await message.message.edit_text(text, reply_markup=keyboard)
        else:
            await message.answer(text, reply_markup=keyboard)
    except TelegramBadRequest:
        pass

async def manage_channel_by_id(message: Message, db_pool: asyncpg.Pool, channel_id: int):
    lang_code = await get_user_language(message.from_user.id, db_pool)
    async with db_pool.acquire() as conn:
        channel_name = await conn.fetchval("SELECT channel_name FROM channels WHERE channel_id = $1", channel_id)
    
    keyboard = await get_channel_manage_keyboard(channel_id, lang_code, db_pool)
    await message.answer(get_text(lang_code, 'manage_channel_title', channel_name=channel_name), reply_markup=keyboard)

# --- [ЕДИНАЯ ЛОГИКА ДОБАВЛЕНИЯ КАНАЛА] ---
async def _add_channel_logic(message: Message, bot: Bot, db_pool: asyncpg.Pool, state: FSMContext, channel_id: int | str) -> bool:
    lang_code = await get_user_language(message.from_user.id, db_pool)
    user_id = message.from_user.id
    current_state_str = await state.get_state()
    is_onboarding = current_state_str == AddChannel.waiting_for_input

    try:
        chat_info = await bot.get_chat(channel_id)
        if chat_info.type != 'channel':
            await message.reply(get_text(lang_code, 'forward_from_channel_required'))
            return False

        member = await bot.get_chat_member(chat_info.id, user_id)
        if not isinstance(member, (types.ChatMemberOwner, types.ChatMemberAdministrator)):
            await message.reply(get_text(lang_code, 'user_not_admin_error'))
            return False

        bot_member = await bot.get_chat_member(chat_info.id, bot.id)
        if not isinstance(bot_member, (types.ChatMemberOwner, types.ChatMemberAdministrator)):
            raise PermissionError("Not admin")
        if isinstance(bot_member, types.ChatMemberAdministrator) and not bot_member.can_post_messages:
            raise PermissionError("No post messages permission")

        async with db_pool.acquire() as connection:
            await connection.execute(
                "INSERT INTO channels (channel_id, channel_name, owner_id) VALUES ($1, $2, $3) "
                "ON CONFLICT (channel_id) DO UPDATE SET channel_name = EXCLUDED.channel_name, owner_id = EXCLUDED.owner_id;",
                chat_info.id, chat_info.title, user_id
            )
        
        await message.reply(get_text(lang_code, 'channel_added_success', channel_title=chat_info.title))
        
        if is_onboarding:
            await state.set_state(Onboarding.waiting_for_passport)
            await state.update_data(channel_id=chat_info.id)
            keyboard = get_onboarding_after_channel_keyboard(lang_code, chat_info.id)
            await message.answer(
                get_text(lang_code, 'onboarding_step2_passport'),
                reply_markup=keyboard
            )
        else:
            await state.clear()
            await show_channels_menu(message, db_pool)
        
        return True

    except TelegramBadRequest:
        await message.reply(get_text(lang_code, 'channel_not_found_error'))
        return False
    except PermissionError as e:
        if str(e) == "Not admin": await message.reply(get_text(lang_code, 'bot_not_admin_error'))
        elif str(e) == "No post messages permission": await message.reply(get_text(lang_code, 'bot_no_post_permission_error'))
        return False
    except Exception as e:
        logging.error(f"Неизвестная ошибка при добавлении канала: {e}", exc_info=True)
        await message.reply(get_text(lang_code, 'generic_error'))
        return False

# --- [ЕДИНЫЙ ПРОЦЕСС ДОБАВЛЕНИЯ КАНАЛА] ---

# Шаг 1: Пользователь нажимает кнопку "Добавить канал"
@router.callback_query(F.data == "add_channel_start")
async def start_add_channel_process(callback: CallbackQuery, state: FSMContext):
    lang_code = await get_user_language(callback.from_user.id, state.storage)
    await state.set_state(AddChannel.waiting_for_input)
    keyboard = get_cancel_add_channel_keyboard(lang_code)
    await callback.message.edit_text(get_text(lang_code, 'add_channel_unified_prompt'), reply_markup=keyboard)
    await callback.answer()

# Шаг 2: Пользователь отправляет что-либо (пересылку или текст)
@router.message(AddChannel.waiting_for_input)
async def process_any_input_for_channel(message: Message, bot: Bot, db_pool: asyncpg.Pool, state: FSMContext):
    # Вариант А: Пересланное сообщение
    if message.forward_from_chat and message.forward_from_chat.type == 'channel':
        await _add_channel_logic(message, bot, db_pool, state, message.forward_from_chat.id)
        return

    # Вариант Б: Текст (ссылка, юзернейм или ID)
    if message.text:
        text_input = message.text.strip()
        target_id: str | int
        
        if text_input.startswith("-100") and text_input[1:].isdigit() and len(text_input) > 13:
            target_id = int(text_input)
        elif text_input.startswith(('https://t.me/', '@', 't.me/')):
            target_id = text_input
        else:
            lang_code = await get_user_language(message.from_user.id, db_pool)
            await message.reply(get_text(lang_code, 'invalid_channel_id_error'))
            return
        
        await _add_channel_logic(message, bot, db_pool, state, target_id)
        return

    # Если прислали не текст и не пересылку (например, фото)
    lang_code = await get_user_language(message.from_user.id, db_pool)
    await message.reply(get_text(lang_code, 'invalid_channel_id_error'))

# Шаг 3: Пользователь нажимает "Отмена"
@router.callback_query(AddChannel.waiting_for_input, F.data == "cancel_add_channel")
async def cancel_add_channel_process(callback: CallbackQuery, state: FSMContext, db_pool: asyncpg.Pool):
    await state.clear()
    await callback.answer()
    await show_channels_menu(callback, db_pool)


# --- ОБРАБОТЧИКИ МЕНЮ КАНАЛОВ, ПАГИНАЦИИ, ПАПОК, ПЕРЕМЕЩЕНИЯ ---

@router.callback_query(F.data == "my_channels_menu")
async def my_channels_callback_handler(callback: CallbackQuery, db_pool: asyncpg.Pool):
    await show_channels_menu(callback, db_pool)
    await callback.answer()

@router.callback_query(F.data.startswith("channels_page_"))
async def channels_page_callback(callback: CallbackQuery, db_pool: asyncpg.Pool):
    page = int(callback.data.split("_")[2])
    await show_channels_menu(callback, db_pool, page=page)
    await callback.answer()

@router.callback_query(F.data.startswith("folder_view_"))
async def view_folder_handler(callback: CallbackQuery, db_pool: asyncpg.Pool):
    folder_id = int(callback.data.split("_")[2])
    user_id = callback.from_user.id
    lang_code = await get_user_language(user_id, db_pool)
    
    async with db_pool.acquire() as conn:
        folder_name = await conn.fetchval("SELECT folder_name FROM folders WHERE id = $1 AND owner_id = $2", folder_id, user_id)

    if not folder_name:
        await callback.answer("Folder not found!", show_alert=True)
        return

    keyboard = await get_folder_view_keyboard(folder_id, user_id, lang_code, db_pool)
    await callback.message.edit_text(get_text(lang_code, 'folder_view_title', folder_name=folder_name), reply_markup=keyboard)
    await callback.answer()

@router.callback_query(F.data.startswith("folder_delete_request_"))
async def folder_delete_request_handler(callback: CallbackQuery, db_pool: asyncpg.Pool):
    folder_id = int(callback.data.split("_")[3])
    lang_code = await get_user_language(callback.from_user.id, db_pool)
    async with db_pool.acquire() as conn:
        folder_name = await conn.fetchval("SELECT folder_name FROM folders WHERE id = $1", folder_id)
    
    text = get_text(lang_code, 'confirm_delete_folder_prompt', folder_name=folder_name)
    keyboard = get_confirmation_keyboard(
        action_callback=f"folder_delete_confirm_{folder_id}",
        lang_code=lang_code,
        back_callback=f"folder_view_{folder_id}"
    )
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()

@router.callback_query(F.data.startswith("folder_delete_confirm_"))
async def folder_delete_confirm_handler(callback: CallbackQuery, db_pool: asyncpg.Pool):
    folder_id = int(callback.data.split("_")[3])
    lang_code = await get_user_language(callback.from_user.id, db_pool)
    
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            folder_name = await conn.fetchval("SELECT folder_name FROM folders WHERE id = $1", folder_id)
            await conn.execute("UPDATE channels SET folder_id = NULL WHERE folder_id = $1", folder_id)
            await conn.execute("DELETE FROM folders WHERE id = $1", folder_id)
    
    await callback.answer(get_text(lang_code, 'folder_deleted_success', folder_name=folder_name), show_alert=True)
    await show_channels_menu(callback, db_pool)

@router.callback_query(F.data.startswith("channel_manage_"))
async def manage_channel_handler(callback: CallbackQuery, db_pool: asyncpg.Pool):
    channel_id = int(callback.data.split("_")[2])
    lang_code = await get_user_language(callback.from_user.id, db_pool)
    async with db_pool.acquire() as conn:
        channel_name = await conn.fetchval("SELECT channel_name FROM channels WHERE channel_id = $1", channel_id)
    
    keyboard = await get_channel_manage_keyboard(channel_id, lang_code, db_pool)
    await callback.message.edit_text(get_text(lang_code, 'manage_channel_title', channel_name=channel_name), reply_markup=keyboard)
    await callback.answer()

@router.callback_query(F.data.startswith("channel_move_"))
async def move_channel_handler(callback: CallbackQuery, db_pool: asyncpg.Pool):
    channel_id = int(callback.data.split("_")[2])
    user_id = callback.from_user.id
    lang_code = await get_user_language(user_id, db_pool)
    
    keyboard = await get_channel_move_keyboard(channel_id, user_id, lang_code, db_pool)
    if not keyboard:
        await callback.answer(get_text(lang_code, 'no_folders_to_move_to'), show_alert=True)
        return
    
    async with db_pool.acquire() as conn:
        channel_name = await conn.fetchval("SELECT channel_name FROM channels WHERE channel_id = $1", channel_id)
        
    await callback.message.edit_text(get_text(lang_code, 'choose_folder_to_move', channel_name=channel_name), reply_markup=keyboard)
    await callback.answer()

@router.callback_query(F.data.startswith("channel_moveto_"))
async def move_channel_to_folder_handler(callback: CallbackQuery, db_pool: asyncpg.Pool):
    _, _, channel_id_str, folder_id_str = callback.data.split("_")
    channel_id, folder_id = int(channel_id_str), int(folder_id_str)
    lang_code = await get_user_language(callback.from_user.id, db_pool)

    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE channels SET folder_id = $1 WHERE channel_id = $2", folder_id, channel_id)
        channel_name = await conn.fetchval("SELECT channel_name FROM channels WHERE channel_id = $1", channel_id)
        folder_name = await conn.fetchval("SELECT folder_name FROM folders WHERE id = $1", folder_id)

    await callback.answer(get_text(lang_code, 'channel_moved_success', channel_name=channel_name, folder_name=folder_name), show_alert=True)
    await show_channels_menu(callback, db_pool)

@router.callback_query(F.data.startswith("channel_removefromfolder_"))
async def remove_channel_from_folder_handler(callback: CallbackQuery, db_pool: asyncpg.Pool):
    channel_id = int(callback.data.split("_")[2])
    lang_code = await get_user_language(callback.from_user.id, db_pool)
    
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE channels SET folder_id = NULL WHERE channel_id = $1", channel_id)
        channel_name = await conn.fetchval("SELECT channel_name FROM channels WHERE channel_id = $1", channel_id)
        
    await callback.answer(get_text(lang_code, 'channel_removed_from_folder_success', channel_name=channel_name), show_alert=True)
    await show_channels_menu(callback, db_pool)

@router.callback_query(F.data.startswith("channel_delete_request_"))
async def channel_delete_request_handler(callback: CallbackQuery, db_pool: asyncpg.Pool):
    channel_id = int(callback.data.split("_")[3])
    lang_code = await get_user_language(callback.from_user.id, db_pool)
    async with db_pool.acquire() as conn:
        channel_name = await conn.fetchval("SELECT channel_name FROM channels WHERE channel_id = $1", channel_id)
    
    text = get_text(lang_code, 'confirm_delete_channel_prompt', channel_name=channel_name)
    keyboard = get_confirmation_keyboard(
        action_callback=f"channel_delete_confirm_{channel_id}",
        lang_code=lang_code,
        back_callback=f"channel_manage_{channel_id}"
    )
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()

@router.callback_query(F.data.startswith("channel_delete_confirm_"))
async def channel_delete_confirm_handler(callback: CallbackQuery, db_pool: asyncpg.Pool):
    channel_id = int(callback.data.split("_")[3])
    lang_code = await get_user_language(callback.from_user.id, db_pool)
    
    async with db_pool.acquire() as conn:
        channel_name = await conn.fetchval("DELETE FROM channels WHERE channel_id = $1 RETURNING channel_name", channel_id)
        
    await callback.answer(get_text(lang_code, 'channel_deleted_success', channel_name=channel_name), show_alert=True)
    await show_channels_menu(callback, db_pool)

# --- БЛОК УПРАВЛЕНИЯ ПАСПОРТОМ СТИЛЯ ---

@router.callback_query(F.data.startswith("channel_passport_create_"))
async def start_style_passport_creation_entry(callback: CallbackQuery, state: FSMContext, db_pool: asyncpg.Pool):
    channel_id = int(callback.data.split("_")[3])
    lang_code = await get_user_language(callback.from_user.id, db_pool)
    await start_style_passport_creation(callback, state, channel_id, lang_code)

@router.callback_query(F.data.startswith("channel_passport_"))
async def manage_style_passport(callback: CallbackQuery, state: FSMContext, db_pool: asyncpg.Pool):
    channel_id = int(callback.data.split("_")[2])
    user_id = callback.from_user.id
    lang_code = await get_user_language(user_id, db_pool)

    async with db_pool.acquire() as conn:
        channel_data = await conn.fetchrow(
            "SELECT channel_name, style_passport, style_passport_updated_at FROM channels WHERE channel_id = $1",
            channel_id
        )

    if channel_data['style_passport']:
        if channel_data['style_passport_updated_at'] and \
           (datetime.datetime.now(datetime.timezone.utc) - channel_data['style_passport_updated_at'] < PASSPORT_UPDATE_COOLDOWN):
            
            remaining = PASSPORT_UPDATE_COOLDOWN - (datetime.datetime.now(datetime.timezone.utc) - channel_data['style_passport_updated_at'])
            hours, remainder = divmod(int(remaining.total_seconds()), 3600)
            minutes, _ = divmod(remainder, 60)
            
            await callback.answer(get_text(lang_code, 'style_passport_update_too_soon', hours=hours, minutes=minutes), show_alert=True)
            return

        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(
            text=get_text(lang_code, 'update_style_passport_button'),
            callback_data=f"channel_passport_create_{channel_id}"
        ))
        builder.row(InlineKeyboardButton(
            text=get_text(lang_code, 'back_to_channels_button'),
            callback_data=f"channel_manage_{channel_id}"
        ))
        await callback.message.edit_text(
            get_text(lang_code, 'current_style_passport', channel_name=channel_data['channel_name'], passport_text=channel_data['style_passport']),
            reply_markup=builder.as_markup()
        )
    else:
        await start_style_passport_creation(callback, state, channel_id, lang_code)

async def start_style_passport_creation(callback: CallbackQuery, state: FSMContext, channel_id: int, lang_code: str):
    await state.set_state(ChannelStylePassportCreation.collecting_posts)
    await state.update_data(posts=[], char_count=0, channel_id=channel_id)

    keyboard = get_style_passport_creation_keyboard(lang_code)
    text = get_text(lang_code, 'no_style_passport_yet') + "\n\n" + \
           get_text(lang_code, 'style_passport_creation_intro', post_count=0, char_count=0, max_chars=MAX_CHARS_FOR_PASSPORT)
    
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()

@router.message(ChannelStylePassportCreation.collecting_posts)
async def collect_post_for_passport(message: Message, state: FSMContext, db_pool: asyncpg.Pool):
    lang_code = await get_user_language(message.from_user.id, db_pool)
    data = await state.get_data()
    
    current_posts = data.get('posts', [])
    current_chars = data.get('char_count', 0)

    if len(current_posts) >= MAX_POSTS_FOR_PASSPORT or current_chars + len(message.text or "") > MAX_CHARS_FOR_PASSPORT:
        await message.reply(get_text(lang_code, 'style_passport_limit_exceeded'))
        return
        
    post_text = message.text or message.caption or ""
    if not post_text: return

    current_posts.append(post_text)
    new_char_count = current_chars + len(post_text)
    await state.update_data(posts=current_posts, char_count=new_char_count)

    await message.reply(
        get_text(lang_code, 'style_passport_post_accepted', 
                 post_count=len(current_posts), 
                 char_count=new_char_count, 
                 max_chars=MAX_CHARS_FOR_PASSPORT)
    )

@router.callback_query(ChannelStylePassportCreation.collecting_posts, F.data == "style_passport_done")
async def process_style_passport(callback: CallbackQuery, state: FSMContext, db_pool: asyncpg.Pool):
    user_id = callback.from_user.id
    lang_code = await get_user_language(user_id, db_pool)
    data = await state.get_data()
    channel_id = data.get('channel_id')
    
    posts_text = "\n\n---\n\n".join(data.get('posts', []))
    if not posts_text:
        await state.clear()
        await callback.message.edit_text(get_text(lang_code, 'style_passport_creation_cancelled'))
        await callback.answer("Вы не отправили ни одного поста для анализа.", show_alert=True)
        return

    await callback.message.edit_text(get_text(lang_code, 'style_passport_generating'))
    
    success, passport_text = await generate_style_passport_from_text(posts_text)

    if success:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE channels SET style_passport = $1, style_passport_updated_at = NOW() WHERE channel_id = $2",
                passport_text, channel_id
            )
        
        await callback.message.edit_text(
            get_text(lang_code, 'style_passport_created_success', passport_text=passport_text)
        )
    else:
        await callback.message.edit_text(f"Произошла ошибка при создании паспорта: {passport_text}")
    
    await callback.answer()
    
    current_onboarding_state = await state.get_state()
    if current_onboarding_state == Onboarding.waiting_for_passport:
        # TODO: Завершить онбординг, перейдя к шагу с описанием
        await state.clear() # Временная заглушка
        await manage_channel_by_id(callback.message, db_pool, channel_id)
    else:
        await state.clear()
        await manage_channel_by_id(callback.message, db_pool, channel_id)


@router.callback_query(ChannelStylePassportCreation.collecting_posts, F.data == "style_passport_cancel")
async def cancel_style_passport_creation(callback: CallbackQuery, state: FSMContext, db_pool: asyncpg.Pool):
    lang_code = await get_user_language(callback.from_user.id, db_pool)
    await state.clear()
    await callback.message.edit_text(get_text(lang_code, 'style_passport_creation_cancelled'))
    await callback.answer()

# --- БЛОК УПРАВЛЕНИЯ ОПИСАНИЕМ ДЕЯТЕЛЬНОСТИ ---

@router.callback_query(F.data.startswith("channel_description_create_"))
async def start_description_input_entry(callback: CallbackQuery, state: FSMContext, db_pool: asyncpg.Pool):
    channel_id = int(callback.data.split("_")[3])
    lang_code = await get_user_language(callback.from_user.id, db_pool)
    await start_description_input(callback, state, channel_id, lang_code)

@router.callback_query(F.data.startswith("channel_description_"))
async def manage_activity_description(callback: CallbackQuery, state: FSMContext, db_pool: asyncpg.Pool):
    channel_id = int(callback.data.split("_")[2])
    user_id = callback.from_user.id
    lang_code = await get_user_language(user_id, db_pool)

    async with db_pool.acquire() as conn:
        channel_data = await conn.fetchrow(
            "SELECT channel_name, activity_description FROM channels WHERE channel_id = $1",
            channel_id
        )

    if channel_data['activity_description']:
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(
            text=get_text(lang_code, 'update_activity_description_button'),
            callback_data=f"channel_description_create_{channel_id}"
        ))
        builder.row(InlineKeyboardButton(
            text=get_text(lang_code, 'back_to_channels_button'),
            callback_data=f"channel_manage_{channel_id}"
        ))
        await callback.message.edit_text(
            get_text(lang_code, 'current_activity_description', channel_name=channel_data['channel_name'], description_text=channel_data['activity_description']),
            reply_markup=builder.as_markup()
        )
    else:
        await start_description_input(callback, state, channel_id, lang_code)

async def start_description_input(callback: CallbackQuery, state: FSMContext, channel_id: int, lang_code: str):
    await state.set_state(ChannelDescription.waiting_for_description)
    await state.update_data(channel_id=channel_id)
    await callback.message.edit_text(get_text(lang_code, 'enter_activity_description_prompt', max_chars=MAX_CHARS_FOR_DESCRIPTION))
    await callback.answer()

@router.message(ChannelDescription.waiting_for_description)
async def process_activity_description(message: Message, state: FSMContext, db_pool: asyncpg.Pool):
    lang_code = await get_user_language(message.from_user.id, db_pool)
    data = await state.get_data()
    channel_id = data.get('channel_id')
    
    description_text = sanitize_text(message.text)
    if not is_valid_description(description_text):
        await message.reply(get_text(lang_code, 'description_too_long_error', max_chars=MAX_CHARS_FOR_DESCRIPTION))
        return

    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE channels SET activity_description = $1 WHERE channel_id = $2",
            description_text, channel_id
        )
    
    await state.clear()
    await message.reply(get_text(lang_code, 'activity_description_saved'))
    
    await manage_channel_by_id(message, db_pool, channel_id)

# --- БЛОК УПРАВЛЕНИЯ ЯЗЫКОМ ГЕНЕРАЦИИ ---

@router.callback_query(F.data.startswith("channel_language_"))
async def manage_generation_language(callback: CallbackQuery, state: FSMContext, db_pool: asyncpg.Pool):
    channel_id = int(callback.data.split("_")[2])
    user_id = callback.from_user.id
    lang_code = await get_user_language(user_id, db_pool)

    async with db_pool.acquire() as conn:
        current_lang = await conn.fetchval(
            "SELECT generation_language FROM channels WHERE channel_id = $1", channel_id
        ) or lang_code
    
    await state.set_state(ChannelLanguage.waiting_for_language)
    await state.update_data(channel_id=channel_id)
    
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text=get_text(lang_code, 'back_to_channels_button'),
        callback_data=f"channel_manage_{channel_id}"
    ))

    await callback.message.edit_text(
        get_text(lang_code, 'choose_generation_language', current_lang=current_lang),
        reply_markup=builder.as_markup()
    )
    await callback.answer()

@router.message(ChannelLanguage.waiting_for_language)
async def set_generation_language(message: Message, state: FSMContext, db_pool: asyncpg.Pool):
    data = await state.get_data()
    channel_id = data.get('channel_id')
    new_lang = sanitize_text(message.text)
    lang_code = await get_user_language(message.from_user.id, db_pool)

    if not new_lang or len(new_lang) > 50:
        await message.reply("Некорректное название языка.")
        return

    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE channels SET generation_language = $1 WHERE channel_id = $2",
            new_lang, channel_id
        )
    
    await state.clear()
    await message.reply(get_text(lang_code, 'generation_language_updated', new_lang=new_lang))
    
    await manage_channel_by_id(message, db_pool, channel_id)

# --- ЛОГИКА СОЗДАНИЯ ПАПКИ ---

@router.callback_query(F.data == "create_folder")
async def create_folder_callback(callback: CallbackQuery, state: FSMContext, db_pool: asyncpg.Pool):
    lang_code = await get_user_language(callback.from_user.id, db_pool)
    await callback.message.edit_text(get_text(lang_code, 'enter_folder_name_prompt'))
    await state.set_state(FolderCreation.waiting_for_name)
    await callback.answer()

@router.message(FolderCreation.waiting_for_name)
async def folder_name_handler(message: Message, state: FSMContext, db_pool: asyncpg.Pool):
    await state.clear()
    lang_code = await get_user_language(message.from_user.id, db_pool)
    
    folder_name = sanitize_text(message.text)
    if not is_valid_name(folder_name):
        await message.reply(get_text(lang_code, 'invalid_name_error'))
        await show_channels_menu(message, db_pool)
        return
    
    try:
        async with db_pool.acquire() as connection:
            await connection.execute("INSERT INTO folders (owner_id, folder_name) VALUES ($1, $2)", message.from_user.id, folder_name)
        await message.reply(get_text(lang_code, 'folder_created_success', folder_name=folder_name))
    except asyncpg.UniqueViolationError:
        await message.reply(get_text(lang_code, 'folder_name_exists_error'))
    except Exception:
        await message.reply(get_text(lang_code, 'generic_error'))
    
    await show_channels_menu(message, db_pool)