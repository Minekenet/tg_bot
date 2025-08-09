from aiogram import Router, F, Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.utils.states import SupportRequest
from bot.utils.localization import get_text
from bot import config # Импортируем наш конфиг

router = Router()

# Используем список админов из централизованного конфига
ADMIN_IDS = config.ADMINS

# Шаг 1: Пользователь нажимает кнопку "Техподдержка"
@router.callback_query(F.data == "support")
async def start_support_request(callback: CallbackQuery, state: FSMContext):
    lang_code = callback.from_user.language_code or 'ru'
    
    await state.set_state(SupportRequest.waiting_for_message)
    
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=get_text(lang_code, 'cancel_button'), callback_data="cancel_support_request"))
    
    await callback.message.edit_text(
        get_text(lang_code, 'support_request_prompt'),
        reply_markup=builder.as_markup()
    )
    await callback.answer()

# Шаг 2: Пользователь отправляет свое сообщение
@router.message(SupportRequest.waiting_for_message)
async def process_support_message(message: Message, state: FSMContext, bot: Bot):
    lang_code = message.from_user.language_code or 'ru'
    
    if not ADMIN_IDS:
        await message.answer(get_text(lang_code, 'support_not_configured_error'))
        await state.clear()
        return

    # Формируем красивое сообщение для админов
    user_info = (
        f"<b>Новое обращение в техподдержку!</b>\n\n"
        f"<b>От:</b> {message.from_user.full_name}\n"
        f"<b>Username:</b> @{message.from_user.username or 'N/A'}\n"
        f"<b>User ID:</b> <code>{message.from_user.id}</code>"
    )

    # Клавиатура для ответа админа
    reply_kb = InlineKeyboardBuilder()
    reply_kb.row(InlineKeyboardButton(
        text="Ответить пользователю", 
        callback_data=f"admin_reply_to_{message.from_user.id}"
    ))

    # Рассылаем сообщение всем админам
    for admin_id in ADMIN_IDS:
        try:
            # Сначала пересылаем оригинальное сообщение пользователя
            await bot.forward_message(chat_id=admin_id, from_chat_id=message.chat.id, message_id=message.message_id)
            # Затем отправляем сообщение с информацией и кнопкой ответа
            await bot.send_message(admin_id, user_info, reply_markup=reply_kb.as_markup())
        except Exception as e:
            print(f"Не удалось отправить обращение админу {admin_id}: {e}")

    await message.answer(get_text(lang_code, 'support_message_sent_success'))
    await state.clear()

# Шаг 3: Админ нажимает "Ответить пользователю"
@router.callback_query(F.data.startswith("admin_reply_to_"))
async def prompt_admin_for_reply(callback: CallbackQuery, state: FSMContext):
    # Проверяем, что кнопку нажал именно админ
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("У вас нет прав для этого действия.", show_alert=True)
        return

    user_id_to_reply = int(callback.data.split("_")[-1])
    
    await state.set_state(SupportRequest.waiting_for_reply_from_admin)
    await state.update_data(user_id_to_reply=user_id_to_reply)
    
    await callback.message.answer(f"Введите ваш ответ для пользователя с ID <code>{user_id_to_reply}</code>. Он будет отправлен от имени бота.")
    await callback.answer()

# Шаг 4: Админ отправляет ответ, бот пересылает его пользователю
@router.message(SupportRequest.waiting_for_reply_from_admin)
async def send_reply_to_user(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    user_id = data.get('user_id_to_reply')
    
    if not user_id:
        await message.answer("Произошла ошибка, ID пользователя для ответа не найден.")
        await state.clear()
        return

    try:
        # Формируем ответ для пользователя
        reply_text = "<b>Ответ от техподдержки:</b>\n\n" + message.text
        await bot.send_message(user_id, reply_text)
        await message.answer(f"✅ Ваш ответ успешно отправлен пользователю <code>{user_id}</code>.")
    except Exception as e:
        await message.answer(f"❌ Не удалось отправить ответ пользователю <code>{user_id}</code>. Возможно, он заблокировал бота.\nОшибка: {e}")
    
    await state.clear()

# Обработка отмены
@router.callback_query(F.data == "cancel_support_request")
async def cancel_support(callback: CallbackQuery, state: FSMContext):
    lang_code = callback.from_user.language_code or 'ru'
    await state.clear()
    await callback.message.edit_text(get_text(lang_code, 'action_cancelled'))
    await callback.answer()