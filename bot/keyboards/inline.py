import asyncpg
import datetime
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from bot.utils.localization import get_text

CHANNELS_PER_PAGE = 5

def language_selection_keyboard() -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Русский 🇷🇺", callback_data="lang_ru"),
                InlineKeyboardButton(text="English 🇬🇧", callback_data="lang_en"),
            ]
        ]
    )
    return keyboard

def get_main_menu_keyboard(lang_code: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text=get_text(lang_code, 'my_channels_button'),
        callback_data="my_channels_menu"
    ))
    builder.row(InlineKeyboardButton(
        text=get_text(lang_code, 'subscription_button'),
        callback_data="subscription"
    ))
    builder.row(InlineKeyboardButton(
        text=get_text(lang_code, 'support_button'),
        callback_data="support"
    ))
    return builder.as_markup()

async def get_channels_keyboard(user_id: int, lang_code: str, db_pool: asyncpg.Pool, page: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    async with db_pool.acquire() as connection:
        folders = await connection.fetch("SELECT id, folder_name FROM folders WHERE owner_id = $1 ORDER BY folder_name", user_id)
        for folder in folders:
            builder.row(InlineKeyboardButton(
                text=f"📁 {folder['folder_name']}",
                callback_data=f"folder_view_{folder['id']}"
            ))

        offset = page * CHANNELS_PER_PAGE
        root_channels = await connection.fetch(
            "SELECT channel_id, channel_name FROM channels WHERE owner_id = $1 AND folder_id IS NULL ORDER BY channel_name LIMIT $2 OFFSET $3",
            user_id, CHANNELS_PER_PAGE, offset
        )
        for channel in root_channels:
            builder.row(InlineKeyboardButton(
                text=f"📢 {channel['channel_name']}",
                callback_data=f"channel_manage_{channel['channel_id']}"
            ))
        
        total_root_channels = await connection.fetchval("SELECT COUNT(*) FROM channels WHERE owner_id = $1 AND folder_id IS NULL", user_id)
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton(text=get_text(lang_code, 'back_button'), callback_data=f"channels_page_{page-1}"))
        if (page + 1) * CHANNELS_PER_PAGE < total_root_channels:
            nav_buttons.append(InlineKeyboardButton(text=get_text(lang_code, 'forward_button'), callback_data=f"channels_page_{page+1}"))
        if nav_buttons:
            builder.row(*nav_buttons)

    builder.row(InlineKeyboardButton(text=get_text(lang_code, 'add_channel_button'), callback_data="add_channel"))
    builder.row(InlineKeyboardButton(text=get_text(lang_code, 'create_folder_button'), callback_data="create_folder"))
    builder.row(InlineKeyboardButton(text=get_text(lang_code, 'back_to_main_menu_button'), callback_data="back_to_main_menu"))
    
    return builder.as_markup()

async def get_folder_view_keyboard(folder_id: int, user_id: int, lang_code: str, db_pool: asyncpg.Pool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    async with db_pool.acquire() as connection:
        channels_in_folder = await connection.fetch("SELECT channel_id, channel_name FROM channels WHERE owner_id = $1 AND folder_id = $2 ORDER BY channel_name", user_id, folder_id)
    
    for channel in channels_in_folder:
        builder.row(InlineKeyboardButton(
            text=f"📢 {channel['channel_name']}",
            callback_data=f"channel_manage_{channel['channel_id']}"
        ))
    
    builder.row(InlineKeyboardButton(text=get_text(lang_code, 'delete_folder_button'), callback_data=f"folder_delete_request_{folder_id}"))
    builder.row(InlineKeyboardButton(text=get_text(lang_code, 'back_to_channels_button'), callback_data="my_channels_menu"))
    return builder.as_markup()

async def get_channel_manage_keyboard(channel_id: int, lang_code: str, db_pool: asyncpg.Pool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    async with db_pool.acquire() as connection:
        channel_info = await connection.fetchrow("SELECT folder_id FROM channels WHERE channel_id = $1", channel_id)
    
    builder.row(InlineKeyboardButton(
        text=get_text(lang_code, 'manage_style_passport_button'), 
        callback_data=f"channel_passport_{channel_id}"
    ))

    if channel_info and channel_info['folder_id'] is not None:
        builder.row(InlineKeyboardButton(text=get_text(lang_code, 'remove_from_folder_button'), callback_data=f"channel_removefromfolder_{channel_id}"))
    else:
        builder.row(InlineKeyboardButton(text=get_text(lang_code, 'move_to_folder_button'), callback_data=f"channel_move_{channel_id}"))
        
    builder.row(InlineKeyboardButton(text=get_text(lang_code, 'delete_channel_button'), callback_data=f"channel_delete_request_{channel_id}"))
    builder.row(InlineKeyboardButton(text=get_text(lang_code, 'back_to_channels_button'), callback_data="my_channels_menu"))
    return builder.as_markup()

async def get_channel_move_keyboard(channel_id: int, user_id: int, lang_code: str, db_pool: asyncpg.Pool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    async with db_pool.acquire() as connection:
        folders = await connection.fetch("SELECT id, folder_name FROM folders WHERE owner_id = $1 ORDER BY folder_name", user_id)
    
    if not folders:
        return None

    for folder in folders:
        builder.row(InlineKeyboardButton(text=f"📁 {folder['folder_name']}", callback_data=f"channel_moveto_{channel_id}_{folder['id']}"))
    
    builder.row(InlineKeyboardButton(text=get_text(lang_code, 'back_to_channels_button'), callback_data=f"channel_manage_{channel_id}"))
    return builder.as_markup()

def get_confirmation_keyboard(action_callback: str, lang_code: str, back_callback: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=get_text(lang_code, 'confirm_action_yes'), callback_data=action_callback),
        InlineKeyboardButton(text=get_text(lang_code, 'confirm_action_no'), callback_data=back_callback)
    )
    return builder.as_markup()

async def get_subscription_keyboard(user_id: int, lang_code: str, db_pool: asyncpg.Pool) -> tuple[InlineKeyboardMarkup, str]:
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO subscriptions (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING", user_id)
        sub = await conn.fetchrow("SELECT * FROM subscriptions WHERE user_id = $1", user_id)

    builder = InlineKeyboardBuilder()
    
    current_plan_name_local = get_text(lang_code, f"plan_{sub['plan_name']}_name")
    text = f"{get_text(lang_code, 'subscription_management_title')}\n\n"
    text += f"{get_text(lang_code, 'your_current_plan', plan_name=current_plan_name_local)}\n"
    text += f"{get_text(lang_code, 'generations_left', count=sub['generations_left'])}\n"
    if sub['expires_at']:
        expires_date_str = sub['expires_at'].strftime('%d.%m.%Y')
        text += f"{get_text(lang_code, 'plan_expires_on', date=expires_date_str)}\n"
    
    text += "\n"

    if sub['plan_name'] == 'free':
        text += f"<u>{get_text(lang_code, 'plan_basic_name')}</u>: {get_text(lang_code, 'plan_basic_desc')}\n"
        builder.row(InlineKeyboardButton(text=get_text(lang_code, 'upgrade_to_basic_button'), callback_data="subscribe_basic"))
    
    if sub['plan_name'] in ['free', 'basic']:
        text += f"<u>{get_text(lang_code, 'plan_pro_name')}</u>: {get_text(lang_code, 'plan_pro_desc')}\n"
        builder.row(InlineKeyboardButton(text=get_text(lang_code, 'upgrade_to_pro_button'), callback_data="subscribe_pro"))

    builder.row(InlineKeyboardButton(text=get_text(lang_code, 'back_to_main_menu_button'), callback_data="back_to_main_menu"))
    
    return builder.as_markup(), text

def get_style_passport_creation_keyboard(lang_code: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=get_text(lang_code, 'style_passport_button_done'), callback_data="style_passport_done"),
        InlineKeyboardButton(text=get_text(lang_code, 'style_passport_button_cancel'), callback_data="style_passport_cancel")
    )
    return builder.as_markup()