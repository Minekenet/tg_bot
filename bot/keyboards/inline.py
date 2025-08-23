import asyncpg
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

def get_welcome_keyboard(lang_code: str, escape_html_chars: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text=get_text(lang_code, 'welcome_continue_button', escape_html_chars=escape_html_chars),
        callback_data="start_onboarding"
    ))
    return builder.as_markup()

def get_main_menu_keyboard(lang_code: str, escape_html_chars: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text=get_text(lang_code, 'my_channels_button', escape_html_chars=escape_html_chars),
        callback_data="my_channels_menu"
    ))
    builder.row(InlineKeyboardButton(
        text=get_text(lang_code, 'subscription_button', escape_html_chars=escape_html_chars),
        callback_data="subscription"
    ))
    builder.row(InlineKeyboardButton(
        text=get_text(lang_code, 'support_button', escape_html_chars=escape_html_chars),
        callback_data="support"
    ))
    return builder.as_markup()

async def get_channels_keyboard(user_id: int, lang_code: str, db_pool: asyncpg.Pool, page: int = 0, escape_html_chars: bool = False) -> InlineKeyboardMarkup:
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
            nav_buttons.append(InlineKeyboardButton(text=get_text(lang_code, 'back_button', escape_html_chars=escape_html_chars), callback_data=f"channels_page_{page-1}"))
        if (page + 1) * CHANNELS_PER_PAGE < total_root_channels:
            nav_buttons.append(InlineKeyboardButton(text=get_text(lang_code, 'forward_button', escape_html_chars=escape_html_chars), callback_data=f"channels_page_{page+1}"))
        if nav_buttons:
            builder.row(*nav_buttons)

    builder.row(InlineKeyboardButton(text=get_text(lang_code, 'add_channel_button', escape_html_chars=escape_html_chars), callback_data="add_channel_start"))
    builder.row(InlineKeyboardButton(text=get_text(lang_code, 'create_folder_button', escape_html_chars=escape_html_chars), callback_data="create_folder"))
    builder.row(InlineKeyboardButton(text=get_text(lang_code, 'back_to_main_menu_button', escape_html_chars=escape_html_chars), callback_data="back_to_main_menu"))
    
    return builder.as_markup()

def get_cancel_add_channel_keyboard(lang_code: str, escape_html_chars: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text=get_text(lang_code, 'cancel_button', escape_html_chars=escape_html_chars),
        callback_data="cancel_add_channel"
    ))
    return builder.as_markup()

async def get_folder_view_keyboard(folder_id: int, user_id: int, lang_code: str, db_pool: asyncpg.Pool, escape_html_chars: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    async with db_pool.acquire() as connection:
        channels_in_folder = await connection.fetch("SELECT channel_id, channel_name FROM channels WHERE owner_id = $1 AND folder_id = $2 ORDER BY channel_name", user_id, folder_id)
    
    for channel in channels_in_folder:
        builder.row(InlineKeyboardButton(
            text=f"📢 {channel['channel_name']}",
            callback_data=f"channel_manage_{channel['channel_id']}"
        ))
    
    builder.row(InlineKeyboardButton(text=get_text(lang_code, 'delete_folder_button', escape_html_chars=escape_html_chars), callback_data=f"folder_delete_request_{folder_id}"))
    builder.row(InlineKeyboardButton(text=get_text(lang_code, 'back_to_channels_button', escape_html_chars=escape_html_chars), callback_data="my_channels_menu"))
    return builder.as_markup()

async def get_channel_manage_keyboard(channel_id: int, lang_code: str, db_pool: asyncpg.Pool, escape_html_chars: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    async with db_pool.acquire() as connection:
        channel_info = await connection.fetchrow("SELECT folder_id FROM channels WHERE channel_id = $1", channel_id)
    
    builder.row(
        InlineKeyboardButton(
            text=get_text(lang_code, 'manage_style_passport_button', escape_html_chars=escape_html_chars), 
            callback_data=f"channel_passport_{channel_id}"
        ),
        InlineKeyboardButton(
            text=get_text(lang_code, 'manage_activity_description_button', escape_html_chars=escape_html_chars), 
            callback_data=f"channel_description_{channel_id}"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=get_text(lang_code, 'manage_scenarios_button', escape_html_chars=escape_html_chars),
            callback_data=f"scenarios_menu_{channel_id}"
        ),
        InlineKeyboardButton(
            text=get_text(lang_code, 'manage_language_button', escape_html_chars=escape_html_chars),
            callback_data=f"channel_language_{channel_id}"
        )
    )

    if channel_info and channel_info['folder_id'] is not None:
        builder.row(InlineKeyboardButton(text=get_text(lang_code, 'remove_from_folder_button', escape_html_chars=escape_html_chars), callback_data=f"channel_removefromfolder_{channel_id}"))
    else:
        builder.row(InlineKeyboardButton(text=get_text(lang_code, 'move_to_folder_button', escape_html_chars=escape_html_chars), callback_data=f"channel_move_{channel_id}"))
        
    builder.row(InlineKeyboardButton(text=get_text(lang_code, 'delete_channel_button', escape_html_chars=escape_html_chars), callback_data=f"channel_delete_request_{channel_id}"))
    builder.row(InlineKeyboardButton(text=get_text(lang_code, 'back_to_channels_button', escape_html_chars=escape_html_chars), callback_data="my_channels_menu"))
    return builder.as_markup()

async def get_channel_move_keyboard(channel_id: int, user_id: int, lang_code: str, db_pool: asyncpg.Pool, escape_html_chars: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    async with db_pool.acquire() as connection:
        folders = await connection.fetch("SELECT id, folder_name FROM folders WHERE owner_id = $1 ORDER BY folder_name", user_id)
    
    if not folders:
        return None

    for folder in folders:
        builder.row(InlineKeyboardButton(text=f"📁 {folder['folder_name']}", callback_data=f"channel_moveto_{channel_id}_{folder['id']}"))
    
    builder.row(InlineKeyboardButton(text=get_text(lang_code, 'back_to_channels_button', escape_html_chars=escape_html_chars), callback_data=f"channel_manage_{channel_id}"))
    return builder.as_markup()

def get_confirmation_keyboard(action_callback: str, lang_code: str, back_callback: str, escape_html_chars: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=get_text(lang_code, 'confirm_action_yes', escape_html_chars=escape_html_chars), callback_data=action_callback),
        InlineKeyboardButton(text=get_text(lang_code, 'confirm_action_no', escape_html_chars=escape_html_chars), callback_data=back_callback)
    )
    return builder.as_markup()

async def get_subscription_keyboard(user_id: int, lang_code: str, db_pool: asyncpg.Pool, escape_html_chars: bool = False) -> tuple[InlineKeyboardMarkup, str]:
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO subscriptions (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING", user_id)
        sub = await conn.fetchrow("SELECT * FROM subscriptions WHERE user_id = $1", user_id)

    builder = InlineKeyboardBuilder()
    
    text = f"{get_text(lang_code, 'subscription_management_title', escape_html_chars=escape_html_chars)}\n\n"
    text += f"*_{get_text(lang_code, 'generations_left', count=sub['generations_left'], escape_html_chars=escape_html_chars)}*\n\n" # Изменено форматирование
    text += f"{get_text(lang_code, 'buy_more_generations_prompt', escape_html_chars=escape_html_chars)}"

    builder.row(InlineKeyboardButton(
        text=get_text(lang_code, 'buy_pack5_button', escape_html_chars=escape_html_chars), 
        callback_data="buy_pack_pack5"
    ))
    builder.row(InlineKeyboardButton(
        text=get_text(lang_code, 'buy_pack30_button', escape_html_chars=escape_html_chars), 
        callback_data="buy_pack_pack30"
    ))
    builder.row(InlineKeyboardButton(
        text=get_text(lang_code, 'buy_pack150_button', escape_html_chars=escape_html_chars), 
        callback_data="buy_pack_pack150"
    ))

    builder.row(InlineKeyboardButton(
        text=get_text(lang_code, 'back_to_main_menu_button', escape_html_chars=escape_html_chars), 
        callback_data="back_to_main_menu"
    ))
    
    return builder.as_markup(), text

def get_style_passport_creation_keyboard(lang_code: str, escape_html_chars: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=get_text(lang_code, 'style_passport_button_done', escape_html_chars=escape_html_chars), callback_data="style_passport_done"),
        InlineKeyboardButton(text=get_text(lang_code, 'style_passport_button_cancel', escape_html_chars=escape_html_chars), callback_data="style_passport_cancel")
    )
    return builder.as_markup()

async def get_scenarios_menu_keyboard(channel_id: int, lang_code: str, db_pool: asyncpg.Pool, escape_html_chars: bool = False) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    
    async with db_pool.acquire() as conn:
        scenarios = await conn.fetch("SELECT id, scenario_name, is_active FROM posting_scenarios WHERE channel_id = $1 ORDER BY scenario_name", channel_id)
        for scenario in scenarios:
            status_icon = "▶️" if scenario['is_active'] else "⏸️"
            # Экранируем scenario['scenario_name'] здесь, т.к. f-строка не "знает" про escape_html_chars
            escaped_scenario_name = get_text(lang_code, scenario['scenario_name'], escape_html_chars=escape_html_chars)
            builder.row(InlineKeyboardButton(
                text=f"{status_icon} {escaped_scenario_name}",
                callback_data=f"scenario_manage_{scenario['id']}"
            ))
    
    builder.row(InlineKeyboardButton(
        text=get_text(lang_code, 'create_scenario_button', escape_html_chars=escape_html_chars),
        callback_data=f"scenario_create_{channel_id}"
    ))
    builder.row(InlineKeyboardButton(
        text=get_text(lang_code, 'back_to_channels_button', escape_html_chars=escape_html_chars),
        callback_data=f"channel_manage_{channel_id}"
    ))
    return builder

def get_media_strategy_keyboard(lang_code: str, escape_html_chars: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    strategies = {
        "text_plus_media": get_text(lang_code, "text_plus_media", escape_html_chars=escape_html_chars),
        "text_only": get_text(lang_code, "text_only", escape_html_chars=escape_html_chars)
    }
    for key, text in strategies.items():
        builder.row(InlineKeyboardButton(text=text, callback_data=f"media_strategy_{key}"))
    return builder.as_markup()

async def get_manage_scenario_keyboard(scenario_id: int, lang_code: str, db_pool: asyncpg.Pool, escape_html_chars: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    async with db_pool.acquire() as conn:
        scenario = await conn.fetchrow("SELECT is_active FROM posting_scenarios WHERE id = $1", scenario_id)

    if scenario['is_active']:
        builder.row(InlineKeyboardButton(
            text=get_text(lang_code, 'pause_scenario_button', escape_html_chars=escape_html_chars),
            callback_data=f"scenario_toggle_active_{scenario_id}"
        ))
    else:
        builder.row(InlineKeyboardButton(
            text=get_text(lang_code, 'resume_scenario_button', escape_html_chars=escape_html_chars),
            callback_data=f"scenario_toggle_active_{scenario_id}"
        ))

    builder.row(
        InlineKeyboardButton(
            text=get_text(lang_code, 'run_scenario_now_button', escape_html_chars=escape_html_chars),
            callback_data=f"scenario_run_now_{scenario_id}"
        ),
        InlineKeyboardButton(
            text=get_text(lang_code, 'edit_scenario_button', escape_html_chars=escape_html_chars),
            callback_data=f"scenario_edit_{scenario_id}"
        )
    )
    builder.row(InlineKeyboardButton(
        text=get_text(lang_code, 'delete_scenario_button', escape_html_chars=escape_html_chars),
        callback_data=f"scenario_delete_request_{scenario_id}"
    ))
    return builder.as_markup()

def get_scenario_edit_keyboard(scenario_id: int, lang_code: str, escape_html_chars: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=get_text(lang_code, "scenario_edit_name", escape_html_chars=escape_html_chars), callback_data=f"s_edit_name_{scenario_id}"))
    builder.row(InlineKeyboardButton(text=get_text(lang_code, "scenario_edit_theme", escape_html_chars=escape_html_chars), callback_data=f"s_edit_theme_{scenario_id}"))
    builder.row(InlineKeyboardButton(text=get_text(lang_code, "scenario_edit_keywords", escape_html_chars=escape_html_chars), callback_data=f"s_edit_keywords_{scenario_id}"))
    builder.row(InlineKeyboardButton(text=get_text(lang_code, "scenario_edit_times", escape_html_chars=escape_html_chars), callback_data=f"s_edit_times_{scenario_id}"))
    builder.row(InlineKeyboardButton(text=get_text(lang_code, "back_to_scenario_management", escape_html_chars=escape_html_chars), callback_data=f"scenario_manage_{scenario_id}"))
    return builder.as_markup()

def get_posting_mode_keyboard(lang_code: str, escape_html_chars: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text=get_text(lang_code, 'mode_direct', escape_html_chars=escape_html_chars),
        callback_data="posting_mode_direct"
    ))
    builder.row(InlineKeyboardButton(
        text=get_text(lang_code, 'mode_moderation', escape_html_chars=escape_html_chars),
        callback_data="posting_mode_moderation"
    ))
    return builder.as_markup()

def get_moderation_keyboard(lang_code: str, channel_id: int, moderation_id: str, escape_html_chars: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=get_text(lang_code, 'publish_button', escape_html_chars=escape_html_chars), callback_data=f"moderation_publish_{moderation_id}"),
        InlineKeyboardButton(text=get_text(lang_code, 'discard_button', escape_html_chars=escape_html_chars), callback_data=f"moderation_discard_{moderation_id}")
    )
    return builder.as_markup()

def get_add_item_keyboard(lang_code: str, action_prefix: str, escape_html_chars: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text=get_text(lang_code, 'button_done_selection', escape_html_chars=escape_html_chars),
        callback_data=f"{action_prefix}_done"
    ))
    return builder.as_markup()

def get_created_scenario_nav_keyboard(lang_code: str, scenario_id: int, escape_html_chars: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text=get_text(lang_code, 'go_to_scenario_settings_button', escape_html_chars=escape_html_chars),
        callback_data=f"scenario_manage_{scenario_id}"
    ))
    return builder.as_markup()

def get_onboarding_after_channel_keyboard(lang_code: str, channel_id: int, escape_html_chars: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text=get_text(lang_code, 'onboarding_create_passport_button', escape_html_chars=escape_html_chars),
        callback_data=f"channel_passport_create_{channel_id}"
    ))
    builder.row(InlineKeyboardButton(
        text=get_text(lang_code, 'onboarding_skip_button', escape_html_chars=escape_html_chars),
        callback_data="skip_onboarding"
    ))
    return builder.as_markup()

def get_onboarding_final_keyboard(lang_code: str, channel_id: int, escape_html_chars: bool = False) -> InlineKeyboardMarkup:
    """Клавиатура после завершения онбординга."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text=get_text(lang_code, 'onboarding_go_to_scenarios_button', escape_html_chars=escape_html_chars),
        callback_data=f"scenarios_menu_{channel_id}"
    ))
    builder.row(InlineKeyboardButton(
        text=get_text(lang_code, 'back_to_main_menu_button', escape_html_chars=escape_html_chars),
        callback_data="back_to_main_menu"
    ))
    return builder.as_markup()