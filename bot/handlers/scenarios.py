# -*- coding: utf-8 -*-
import datetime
import asyncpg
import hashlib
import pytz
from aiogram import Router, F, Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot import config
from bot.utils.localization import get_text
from bot.utils.states import ScenarioCreation, ScenarioEditing
from bot.utils.validation import sanitize_text, is_valid_name, is_valid_keyword
from bot.keyboards.inline import (
    get_scenarios_menu_keyboard, get_source_selection_keyboard, 
    get_media_strategy_keyboard, get_manage_scenario_keyboard, get_confirmation_keyboard,
    get_posting_mode_keyboard, get_add_item_keyboard, get_created_scenario_nav_keyboard,
    get_scenario_edit_keyboard
)
from bot.utils.scheduler import add_job_to_scheduler, remove_job_from_scheduler, process_scenario_job

router = Router()

async def get_user_language(user_id: int, db_pool: asyncpg.Pool) -> str:
    async with db_pool.acquire() as connection:
        return await connection.fetchval("SELECT language_code FROM users WHERE user_id = $1", user_id) or 'ru'

# --- ВХОД В МЕНЮ СЦЕНАРИЕВ ---
@router.callback_query(F.data.startswith("scenarios_menu_"))
async def scenarios_menu_handler(callback: CallbackQuery, db_pool: asyncpg.Pool):
    channel_id = int(callback.data.split("_")[-1])
    lang_code = await get_user_language(callback.from_user.id, db_pool)

    async with db_pool.acquire() as conn:
        channel_info = await conn.fetchrow(
            "SELECT style_passport, activity_description, generation_language, channel_name FROM channels WHERE channel_id = $1",
            channel_id
        )

    passport_ok = bool(channel_info and channel_info['style_passport'])
    description_ok = bool(channel_info and channel_info['activity_description'])
    language_ok = bool(channel_info and channel_info['generation_language'])

    if passport_ok and description_ok and language_ok:
        text = get_text(lang_code, 'scenarios_menu_title', channel_name=channel_info['channel_name'])
        keyboard = await get_scenarios_menu_keyboard(channel_id, lang_code, db_pool)
        await callback.message.edit_text(text, reply_markup=keyboard.as_markup())
    else:
        builder = InlineKeyboardBuilder()
        text = get_text(lang_code, 'scenarios_prerequisites_header') + "\n\n"
        status_icon = "✅" if passport_ok else "❌"
        text += f"{status_icon} {get_text(lang_code, 'prerequisite_passport')}\n"
        if not passport_ok:
            builder.row(InlineKeyboardButton(text=get_text(lang_code, 'setup_passport_button'), callback_data=f"channel_passport_{channel_id}"))
        status_icon = "✅" if description_ok else "❌"
        text += f"{status_icon} {get_text(lang_code, 'prerequisite_description')}\n"
        if not description_ok:
            builder.row(InlineKeyboardButton(text=get_text(lang_code, 'setup_description_button'), callback_data=f"channel_description_{channel_id}"))
        status_icon = "✅" if language_ok else "❌"
        text += f"{status_icon} {get_text(lang_code, 'prerequisite_language')}\n"
        if not language_ok:
            builder.row(InlineKeyboardButton(text=get_text(lang_code, 'setup_language_button'), callback_data=f"channel_language_{channel_id}"))
        builder.row(InlineKeyboardButton(text=get_text(lang_code, 'back_to_channels_button'), callback_data=f"channel_manage_{channel_id}"))
        await callback.message.edit_text(text, reply_markup=builder.as_markup())

    await callback.answer()

# --- FSM СОЗДАНИЯ СЦЕНАРИЯ ---
@router.callback_query(F.data.startswith("scenario_create_"))
async def start_scenario_creation(callback: CallbackQuery, state: FSMContext, db_pool: asyncpg.Pool):
    channel_id = int(callback.data.split("_")[-1])
    lang_code = await get_user_language(callback.from_user.id, db_pool)
    await state.set_state(ScenarioCreation.waiting_for_name)
    await state.update_data(channel_id=channel_id, keywords=[], run_times=[])
    await callback.message.edit_text(get_text(lang_code, 'enter_scenario_name'))
    await callback.answer()

@router.message(ScenarioCreation.waiting_for_name)
async def process_scenario_name(message: Message, state: FSMContext, db_pool: asyncpg.Pool):
    lang_code = await get_user_language(message.from_user.id, db_pool)
    scenario_name = sanitize_text(message.text)
    if not is_valid_name(scenario_name):
        await message.reply(get_text(lang_code, 'invalid_name_error'))
        return
    await state.update_data(name=scenario_name)
    await state.set_state(ScenarioCreation.waiting_for_theme)
    await message.answer(get_text(lang_code, 'enter_scenario_theme'))

@router.message(ScenarioCreation.waiting_for_theme)
async def process_scenario_theme(message: Message, state: FSMContext, db_pool: asyncpg.Pool):
    lang_code = await get_user_language(message.from_user.id, db_pool)
    await state.update_data(theme=sanitize_text(message.text))
    await state.set_state(ScenarioCreation.adding_keywords)
    keyboard = get_add_item_keyboard(lang_code, "keywords")
    msg = await message.answer(get_text(lang_code, 'enter_keywords_prompt_list', keywords_list=""), reply_markup=keyboard, parse_mode="HTML")
    await state.update_data(instruction_message_id=msg.message_id)

@router.message(ScenarioCreation.adding_keywords)
async def process_keyword_addition(message: Message, state: FSMContext, bot: Bot):
    lang_code = await get_user_language(message.from_user.id, None)
    data = await state.get_data()
    keywords = data.get('keywords', [])
    new_keyword = sanitize_text(message.text)
    if not is_valid_keyword(new_keyword):
        await message.delete(); return
    if new_keyword and new_keyword not in keywords:
        keywords.append(new_keyword)
        await state.update_data(keywords=keywords)
    await message.delete()
    keywords_list_str = "\n".join([f"• {kw}" for kw in keywords])
    keyboard = get_add_item_keyboard(lang_code, "keywords")
    try:
        await bot.edit_message_text(
            chat_id=message.chat.id, message_id=data['instruction_message_id'],
            text=get_text(lang_code, 'enter_keywords_prompt_list', keywords_list=keywords_list_str),
            reply_markup=keyboard, parse_mode="HTML"
        )
    except TelegramBadRequest: pass

@router.callback_query(ScenarioCreation.adding_keywords, F.data == "keywords_done")
async def process_keywords_done(callback: CallbackQuery, state: FSMContext, db_pool: asyncpg.Pool):
    lang_code = await get_user_language(callback.from_user.id, db_pool)
    if not (await state.get_data()).get('keywords'):
        await callback.answer(get_text(lang_code, 'keywords_empty_error'), show_alert=True); return
    await state.set_state(ScenarioCreation.choosing_sources)
    await state.update_data(selected_sources=[])
    keyboard = get_source_selection_keyboard(lang_code, [], "source_")
    await callback.message.edit_text(get_text(lang_code, 'choose_sources'), reply_markup=keyboard)
    await callback.answer()

@router.callback_query(ScenarioCreation.choosing_sources, F.data.startswith("source_"))
async def process_source_selection(callback: CallbackQuery, state: FSMContext, db_pool: asyncpg.Pool):
    action, _, source_key = callback.data.partition("_")[2].partition("_")
    lang_code = await get_user_language(callback.from_user.id, db_pool)
    data = await state.get_data()
    selected = data.get('selected_sources', [])
    if action == "done":
        if not selected:
            await callback.answer(get_text(lang_code, 'sources_empty_error'), show_alert=True); return
        await state.update_data(sources=",".join(selected))
        await state.set_state(ScenarioCreation.choosing_media_strategy)
        keyboard = get_media_strategy_keyboard(lang_code)
        await callback.message.edit_text(get_text(lang_code, 'choose_media_strategy'), reply_markup=keyboard)
    elif action == "toggle":
        if source_key in selected: selected.remove(source_key)
        else: selected.append(source_key)
        await state.update_data(selected_sources=selected)
        keyboard = get_source_selection_keyboard(lang_code, selected, "source_")
        try: await callback.message.edit_reply_markup(reply_markup=keyboard)
        except TelegramBadRequest: pass
    await callback.answer()

@router.callback_query(ScenarioCreation.choosing_media_strategy, F.data.startswith("media_strategy_"))
async def process_media_strategy(callback: CallbackQuery, state: FSMContext, db_pool: asyncpg.Pool):
    strategy = callback.data.split("media_strategy_")[1]
    await state.update_data(media_strategy=strategy)
    await state.set_state(ScenarioCreation.choosing_posting_mode)
    lang_code = await get_user_language(callback.from_user.id, db_pool)
    keyboard = get_posting_mode_keyboard(lang_code)
    await callback.message.edit_text(get_text(lang_code, 'choose_posting_mode'), reply_markup=keyboard)
    await callback.answer()

@router.callback_query(ScenarioCreation.choosing_posting_mode, F.data.startswith("posting_mode_"))
async def process_posting_mode(callback: CallbackQuery, state: FSMContext, db_pool: asyncpg.Pool):
    mode = callback.data.split("posting_mode_")[1]
    await state.update_data(posting_mode=mode)
    await state.set_state(ScenarioCreation.adding_times)
    lang_code = await get_user_language(callback.from_user.id, db_pool)
    keyboard = get_add_item_keyboard(lang_code, "times")
    msg = await callback.message.edit_text(get_text(lang_code, 'enter_run_times_prompt_list', times_list=""), reply_markup=keyboard, parse_mode="HTML")
    await state.update_data(instruction_message_id=msg.message_id)
    await callback.answer()

@router.message(ScenarioCreation.adding_times)
async def process_time_addition(message: Message, state: FSMContext, bot: Bot):
    lang_code = await get_user_language(message.from_user.id, None)
    data = await state.get_data()
    times = data.get('run_times', [])
    try:
        time_str = datetime.datetime.strptime(message.text.strip(), "%H:%M").strftime("%H:%M")
        if time_str not in times:
            times.append(time_str)
            await state.update_data(run_times=sorted(times))
    except ValueError: pass
    await message.delete()
    times_list_str = "\n".join([f"• {t}" for t in sorted(times)])
    keyboard = get_add_item_keyboard(lang_code, "times")
    try:
        await bot.edit_message_text(
            chat_id=message.chat.id, message_id=data['instruction_message_id'],
            text=get_text(lang_code, 'enter_run_times_prompt_list', times_list=times_list_str),
            reply_markup=keyboard, parse_mode="HTML"
        )
    except TelegramBadRequest: pass

@router.callback_query(ScenarioCreation.adding_times, F.data == "times_done")
async def process_times_done(callback: CallbackQuery, state: FSMContext, db_pool: asyncpg.Pool):
    lang_code = await get_user_language(callback.from_user.id, db_pool)
    if not (await state.get_data()).get('run_times'):
        await callback.answer(get_text(lang_code, 'times_empty_error'), show_alert=True); return
    await state.set_state(ScenarioCreation.waiting_for_timezone)
    await callback.message.edit_text(get_text(lang_code, 'enter_utc_offset_prompt'))
    await callback.answer()

@router.message(ScenarioCreation.waiting_for_timezone)
async def process_timezone_and_save(message: Message, state: FSMContext, db_pool: asyncpg.Pool, scheduler: AsyncIOScheduler):
    lang_code = await get_user_language(message.from_user.id, db_pool)
    try:
        offset_str = message.text.strip().replace(",", ".")
        offset = float(offset_str)
        if not (-12 <= offset <= 14): raise ValueError
        tz_str = f"Etc/GMT{-int(offset)}" if offset >= 0 else f"Etc/GMT+{-int(offset)}"
        pytz.timezone(tz_str)
    except (ValueError, pytz.UnknownTimeZoneError):
        await message.reply(get_text(lang_code, 'invalid_utc_offset_format')); return
    
    data = await state.get_data()
    async with db_pool.acquire() as conn:
        scenario_id = await conn.fetchval(
            """ INSERT INTO posting_scenarios (owner_id, channel_id, scenario_name, theme, keywords, sources, media_strategy, posting_mode, run_times, timezone)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) RETURNING id """,
            message.from_user.id, data['channel_id'], data['name'], data['theme'], ",".join(data['keywords']), 
            data['sources'], data['media_strategy'], data['posting_mode'], ",".join(data['run_times']), tz_str
        )
    await state.clear()
    new_scenario = await db_pool.fetchrow("SELECT * FROM posting_scenarios WHERE id = $1", scenario_id)
    add_job_to_scheduler(scheduler, dict(new_scenario))
    keyboard = get_created_scenario_nav_keyboard(lang_code, scenario_id)
    await message.answer(get_text(lang_code, 'scenario_created_success_utc', scenario_name=data['name'], utc_offset=f"+{offset}" if offset >= 0 else str(offset)), reply_markup=keyboard)

# --- УПРАВЛЕНИЕ СЦЕНАРИЕМ ---
async def _show_manage_scenario_menu(event: Message | CallbackQuery, db_pool: asyncpg.Pool, state: FSMContext, bot: Bot):
    await state.clear()
    if isinstance(event, CallbackQuery):
        scenario_id = int(event.data.split("_")[-1]); user_id = event.from_user.id; message = event.message
    else:
        data = await state.get_data(); scenario_id = data['scenario_id']; user_id = event.from_user.id; message = event
    lang_code = await get_user_language(user_id, db_pool)
    scenario = await db_pool.fetchrow("SELECT scenario_name, channel_id, is_active FROM posting_scenarios WHERE id = $1", scenario_id)
    if not scenario:
        await bot.send_message(user_id, "Ошибка: сценарий не найден."); return
    status_text = get_text(lang_code, 'scenario_status_active') if scenario['is_active'] else get_text(lang_code, 'scenario_status_paused')
    keyboard = await get_manage_scenario_keyboard(scenario_id, lang_code, db_pool)
    builder = InlineKeyboardBuilder.from_markup(keyboard)
    builder.row(InlineKeyboardButton(text=get_text(lang_code, 'back_to_scenarios_button'), callback_data=f"scenarios_menu_{scenario['channel_id']}"))
    text = get_text(lang_code, 'manage_scenario_title', scenario_name=scenario['scenario_name'], status=status_text)
    if isinstance(event, CallbackQuery):
        try: await message.edit_text(text, reply_markup=builder.as_markup())
        except TelegramBadRequest: pass
        await event.answer()
    else:
        await bot.send_message(chat_id=user_id, text=text, reply_markup=builder.as_markup())

@router.callback_query(F.data.startswith("scenario_manage_"))
async def manage_scenario_handler(callback: CallbackQuery, db_pool: asyncpg.Pool, state: FSMContext, bot: Bot):
    await _show_manage_scenario_menu(callback, db_pool, state, bot)

@router.callback_query(F.data.startswith("scenario_toggle_active_"))
async def toggle_scenario_activity(callback: CallbackQuery, db_pool: asyncpg.Pool, scheduler: AsyncIOScheduler, bot: Bot, state: FSMContext):
    scenario_id = int(callback.data.split("_")[-1])
    lang_code = await get_user_language(callback.from_user.id, db_pool)
    async with db_pool.acquire() as conn:
        old_scenario = await conn.fetchrow("SELECT * FROM posting_scenarios WHERE id = $1", scenario_id)
        if not old_scenario: return
        new_status = not old_scenario['is_active']
        await conn.execute("UPDATE posting_scenarios SET is_active = $1 WHERE id = $2", new_status, scenario_id)
        remove_job_from_scheduler(scheduler, dict(old_scenario))
        if new_status:
            new_scenario_data = await conn.fetchrow("SELECT * FROM posting_scenarios WHERE id = $1", scenario_id)
            add_job_to_scheduler(scheduler, dict(new_scenario_data))
    alert_text = get_text(lang_code, 'scenario_resumed') if new_status else get_text(lang_code, 'scenario_paused')
    await callback.answer(alert_text, show_alert=True)
    await _show_manage_scenario_menu(callback, db_pool, state, bot)

# --- FSM РЕДАКТИРОВАНИЯ СЦЕНАРИЯ ---
@router.callback_query(F.data.startswith("scenario_edit_"))
async def edit_scenario_entry(callback: CallbackQuery, state: FSMContext, db_pool: asyncpg.Pool):
    scenario_id = int(callback.data.split("_")[-1])
    lang_code = await get_user_language(callback.from_user.id, db_pool)
    scenario_name = await db_pool.fetchval("SELECT scenario_name FROM posting_scenarios WHERE id = $1", scenario_id)
    await state.set_state(ScenarioEditing.choosing_option)
    await state.update_data(scenario_id=scenario_id, scenario_name=scenario_name)
    keyboard = get_scenario_edit_keyboard(scenario_id, lang_code)
    await callback.message.edit_text(get_text(lang_code, 'scenario_editing_menu_title', scenario_name=scenario_name), reply_markup=keyboard)
    await callback.answer()

# Редактирование Названия
@router.callback_query(F.data.startswith("s_edit_name_"), ScenarioEditing.choosing_option)
async def edit_scenario_name_prompt(callback: CallbackQuery, state: FSMContext):
    await state.set_state(ScenarioEditing.editing_name); await callback.message.edit_text(get_text(callback.from_user.language_code, 'enter_new_scenario_name')); await callback.answer()
@router.message(ScenarioEditing.editing_name)
async def process_new_scenario_name(message: Message, state: FSMContext, db_pool: asyncpg.Pool, bot: Bot):
    data = await state.get_data(); scenario_id = data['scenario_id']; lang_code = await get_user_language(message.from_user.id, db_pool)
    new_name = sanitize_text(message.text)
    if not is_valid_name(new_name): await message.reply(get_text(lang_code, 'invalid_name_error')); return
    await db_pool.execute("UPDATE posting_scenarios SET scenario_name = $1 WHERE id = $2", new_name, scenario_id)
    await message.answer(get_text(lang_code, 'scenario_name_updated'))
    await _show_manage_scenario_menu(message, db_pool, state, bot)

# Редактирование Темы
@router.callback_query(F.data.startswith("s_edit_theme_"), ScenarioEditing.choosing_option)
async def edit_scenario_theme_prompt(callback: CallbackQuery, state: FSMContext):
    await state.set_state(ScenarioEditing.editing_theme); await callback.message.edit_text(get_text(callback.from_user.language_code, 'enter_new_scenario_theme')); await callback.answer()
@router.message(ScenarioEditing.editing_theme)
async def process_new_scenario_theme(message: Message, state: FSMContext, db_pool: asyncpg.Pool, bot: Bot):
    data = await state.get_data(); scenario_id = data['scenario_id']; lang_code = await get_user_language(message.from_user.id, db_pool)
    new_theme = sanitize_text(message.text)
    await db_pool.execute("UPDATE posting_scenarios SET theme = $1 WHERE id = $2", new_theme, scenario_id)
    await message.answer(get_text(lang_code, 'scenario_theme_updated'))
    await _show_manage_scenario_menu(message, db_pool, state, bot)

# Редактирование Ключевых слов
@router.callback_query(F.data.startswith("s_edit_keywords_"), ScenarioEditing.choosing_option)
async def edit_scenario_keywords_prompt(callback: CallbackQuery, state: FSMContext, db_pool: asyncpg.Pool):
    lang_code = await get_user_language(callback.from_user.id, db_pool)
    data = await state.get_data(); scenario_id = data['scenario_id']
    keywords_str = await db_pool.fetchval("SELECT keywords FROM posting_scenarios WHERE id = $1", scenario_id)
    current_keywords = keywords_str.split(',') if keywords_str else []
    await state.set_state(ScenarioEditing.editing_keywords)
    await state.update_data(keywords=current_keywords)
    keyboard = get_add_item_keyboard(lang_code, "keywords_edit")
    msg = await callback.message.edit_text(get_text(lang_code, 'enter_keywords_prompt_list', keywords_list="\n".join([f"• {kw}" for kw in current_keywords])), reply_markup=keyboard, parse_mode="HTML")
    await state.update_data(instruction_message_id=msg.message_id)
    await callback.answer()
@router.message(ScenarioEditing.editing_keywords)
async def process_keyword_edit_addition(message: Message, state: FSMContext, bot: Bot):
    lang_code = await get_user_language(message.from_user.id, None); data = await state.get_data(); keywords = data.get('keywords', [])
    new_keyword = sanitize_text(message.text)
    if is_valid_keyword(new_keyword) and new_keyword not in keywords: keywords.append(new_keyword)
    await state.update_data(keywords=keywords)
    await message.delete()
    keyboard = get_add_item_keyboard(lang_code, "keywords_edit")
    try:
        await bot.edit_message_text(chat_id=message.chat.id, message_id=data['instruction_message_id'], text=get_text(lang_code, 'enter_keywords_prompt_list', keywords_list="\n".join([f"• {kw}" for kw in keywords])), reply_markup=keyboard, parse_mode="HTML")
    except TelegramBadRequest: pass
@router.callback_query(ScenarioEditing.editing_keywords, F.data == "keywords_edit_done")
async def process_keywords_edit_done(callback: CallbackQuery, state: FSMContext, db_pool: asyncpg.Pool, bot: Bot):
    lang_code = await get_user_language(callback.from_user.id, db_pool); data = await state.get_data(); scenario_id = data['scenario_id']
    if not data.get('keywords'): await callback.answer(get_text(lang_code, 'keywords_empty_error'), show_alert=True); return
    await db_pool.execute("UPDATE posting_scenarios SET keywords = $1 WHERE id = $2", ",".join(data['keywords']), scenario_id)
    await callback.message.delete()
    await callback.answer(get_text(lang_code, 'scenario_keywords_updated'), show_alert=True)
    await _show_manage_scenario_menu(callback, db_pool, state, bot)

# Редактирование Источников
@router.callback_query(F.data.startswith("s_edit_sources_"), ScenarioEditing.choosing_option)
async def edit_scenario_sources_prompt(callback: CallbackQuery, state: FSMContext, db_pool: asyncpg.Pool):
    lang_code = await get_user_language(callback.from_user.id, db_pool); data = await state.get_data(); scenario_id = data['scenario_id']
    sources_str = await db_pool.fetchval("SELECT sources FROM posting_scenarios WHERE id = $1", scenario_id)
    current_sources = sources_str.split(',') if sources_str else []
    await state.set_state(ScenarioEditing.editing_sources)
    await state.update_data(selected_sources=current_sources)
    keyboard = get_source_selection_keyboard(lang_code, current_sources, "s_edit_source_")
    await callback.message.edit_text(get_text(lang_code, 'choose_sources'), reply_markup=keyboard)
    await callback.answer()
@router.callback_query(ScenarioEditing.editing_sources, F.data.startswith("s_edit_source_"))
async def process_source_edit_selection(callback: CallbackQuery, state: FSMContext, db_pool: asyncpg.Pool, bot: Bot):
    action, _, source_key = callback.data.partition("_")[3].partition("_")
    lang_code = await get_user_language(callback.from_user.id, db_pool); data = await state.get_data(); selected = data.get('selected_sources', [])
    if action == "done":
        if not selected: await callback.answer(get_text(lang_code, 'sources_empty_error'), show_alert=True); return
        await db_pool.execute("UPDATE posting_scenarios SET sources = $1 WHERE id = $2", ",".join(selected), data['scenario_id'])
        await callback.answer(get_text(lang_code, 'scenario_sources_updated'), show_alert=True)
        await _show_manage_scenario_menu(callback, db_pool, state, bot)
    elif action == "toggle":
        if source_key in selected: selected.remove(source_key)
        else: selected.append(source_key)
        await state.update_data(selected_sources=selected)
        keyboard = get_source_selection_keyboard(lang_code, selected, "s_edit_source_")
        try: await callback.message.edit_reply_markup(reply_markup=keyboard)
        except TelegramBadRequest: pass
    await callback.answer()

# Редактирование Времени
@router.callback_query(F.data.startswith("s_edit_times_"), ScenarioEditing.choosing_option)
async def edit_scenario_times_prompt(callback: CallbackQuery, state: FSMContext, db_pool: asyncpg.Pool):
    # Этот код уже был в предыдущей версии, просто убеждаемся, что он на месте
    lang_code = await get_user_language(callback.from_user.id, db_pool)
    data = await state.get_data()
    scenario_id = data['scenario_id']
    scenario = await db_pool.fetchrow("SELECT run_times FROM posting_scenarios WHERE id = $1", scenario_id)
    current_times = scenario['run_times'].split(',') if scenario['run_times'] else []
    await state.set_state(ScenarioEditing.editing_times)
    await state.update_data(run_times=current_times)
    times_list_str = "\n".join([f"• {t}" for t in sorted(current_times)]) if current_times else "Пока не задано"
    keyboard = get_add_item_keyboard(lang_code, "times_edit")
    msg = await callback.message.edit_text(get_text(lang_code, 'enter_run_times_prompt_list', times_list=times_list_str), reply_markup=keyboard, parse_mode="HTML")
    await state.update_data(instruction_message_id=msg.message_id)
    await callback.answer()
@router.message(ScenarioEditing.editing_times)
async def process_time_edit_addition(message: Message, state: FSMContext, bot: Bot):
    # Этот код уже был в предыдущей версии
    lang_code = await get_user_language(message.from_user.id, None)
    data = await state.get_data(); times = data.get('run_times', [])
    try:
        time_str = datetime.datetime.strptime(message.text.strip(), "%H:%M").strftime("%H:%M")
        if time_str not in times: times.append(time_str)
        await state.update_data(run_times=sorted(times))
    except ValueError: pass
    await message.delete()
    times_list_str = "\n".join([f"• {t}" for t in sorted(times)]) if times else "Пока не задано"
    keyboard = get_add_item_keyboard(lang_code, "times_edit")
    try:
        await bot.edit_message_text(chat_id=message.chat.id, message_id=data['instruction_message_id'], text=get_text(lang_code, 'enter_run_times_prompt_list', times_list=times_list_str), reply_markup=keyboard, parse_mode="HTML")
    except TelegramBadRequest: pass
@router.callback_query(ScenarioEditing.editing_times, F.data == "times_edit_done")
async def process_times_edit_done(callback: CallbackQuery, state: FSMContext, db_pool: asyncpg.Pool, scheduler: AsyncIOScheduler, bot: Bot):
    # Этот код уже был в предыдущей версии
    lang_code = await get_user_language(callback.from_user.id, db_pool); data = await state.get_data(); scenario_id = data['scenario_id']
    new_times_str = ",".join(data.get('run_times', []))
    await db_pool.execute("UPDATE posting_scenarios SET run_times = $1 WHERE id = $2", new_times_str, scenario_id)
    await callback.message.delete()
    await callback.answer(get_text(lang_code, 'scenario_times_updated'), show_alert=True)
    await _show_manage_scenario_menu(callback, db_pool, state, bot)

# --- ОСТАЛЬНЫЕ ФУНКЦИИ УПРАВЛЕНИЯ ---
@router.callback_query(F.data.startswith("scenario_run_now_"))
async def run_scenario_now_handler(callback: CallbackQuery, db_pool: asyncpg.Pool, scheduler: AsyncIOScheduler):
    scenario_id = int(callback.data.split("_")[-1]); user_id = callback.from_user.id
    lang_code = await get_user_language(user_id, db_pool)
    scenario = await db_pool.fetchrow("SELECT * FROM posting_scenarios WHERE id = $1", scenario_id)
    if not scenario: await callback.answer("Сценарий не найден.", show_alert=True); return
    await callback.message.edit_text(get_text(lang_code, 'generation_started'))
    scheduler.add_job(process_scenario_job, id=f"manual_run_{scenario_id}_{datetime.datetime.now().timestamp()}", trigger='date', kwargs={"scenario_id": scenario_id, "user_id": user_id, "channel_id": scenario['channel_id']})
    await callback.answer()

@router.callback_query(F.data.startswith("scenario_delete_request_"))
async def delete_scenario_request(callback: CallbackQuery, db_pool: asyncpg.Pool):
    scenario_id = int(callback.data.split("_")[-1])
    lang_code = await get_user_language(callback.from_user.id, db_pool)
    scenario_name = await db_pool.fetchval("SELECT scenario_name FROM posting_scenarios WHERE id = $1", scenario_id)
    keyboard = get_confirmation_keyboard(f"scenario_delete_confirm_{scenario_id}", lang_code, f"scenario_manage_{scenario_id}")
    await callback.message.edit_text(get_text(lang_code, 'confirm_delete_scenario', scenario_name=scenario_name), reply_markup=keyboard)
    await callback.answer()

@router.callback_query(F.data.startswith("scenario_delete_confirm_"))
async def delete_scenario_confirm(callback: CallbackQuery, db_pool: asyncpg.Pool, scheduler: AsyncIOScheduler):
    scenario_id = int(callback.data.split("_")[-1])
    scenario = await db_pool.fetchrow("DELETE FROM posting_scenarios WHERE id = $1 RETURNING *", scenario_id)
    if scenario:
        remove_job_from_scheduler(scheduler, dict(scenario))
        lang_code = await get_user_language(callback.from_user.id, db_pool)
        await callback.answer(get_text(lang_code, 'scenario_deleted_success', scenario_name=scenario['scenario_name']), show_alert=True)
        channel_id = scenario['channel_id']
        channel_name = await db_pool.fetchval("SELECT channel_name FROM channels WHERE channel_id = $1", channel_id)
        text = get_text(lang_code, 'scenarios_menu_title', channel_name=channel_name)
        keyboard = await get_scenarios_menu_keyboard(channel_id, lang_code, db_pool)
        await callback.message.edit_text(text, reply_markup=keyboard.as_markup())
    else:
        await callback.answer("Сценарий уже был удален.", show_alert=True)

# --- МОДЕРАЦИЯ ---
@router.callback_query(F.data.startswith("moderation_publish_"))
async def moderation_publish_handler(callback: CallbackQuery, bot: Bot, db_pool: asyncpg.Pool):
    channel_id = int(callback.data.split("_")[-1])
    lang_code = await get_user_language(callback.from_user.id, db_pool)
    try:
        if callback.message.photo:
            await bot.send_photo(chat_id=channel_id, photo=callback.message.photo[-1].file_id, caption=callback.message.caption)
        else:
            await bot.send_message(chat_id=channel_id, text=callback.message.text)
        await callback.message.edit_text(get_text(lang_code, 'moderation_published_success'))
        post_text = callback.message.caption or callback.message.text
        url_start = post_text.rfind('http')
        if url_start != -1:
            url = post_text[url_start:]
            link_hash = hashlib.sha256(url.encode()).hexdigest()
            await db_pool.execute("INSERT INTO published_posts (channel_id, source_url_hash) VALUES ($1, $2) ON CONFLICT DO NOTHING", channel_id, link_hash)
    except Exception as e:
        await callback.message.edit_text(f"Ошибка публикации: {e}")
    await callback.answer()

@router.callback_query(F.data == "moderation_discard")
async def moderation_discard_handler(callback: CallbackQuery, db_pool: asyncpg.Pool):
    lang_code = await get_user_language(callback.from_user.id, db_pool)
    await callback.message.delete()
    await callback.answer(get_text(lang_code, 'moderation_discarded'), show_alert=False)