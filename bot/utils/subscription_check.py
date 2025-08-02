import datetime
import asyncpg

async def check_and_decrement_limit(user_id: int, db_pool: asyncpg.Pool) -> bool:
    """
    Проверяет лимиты пользователя. Если все в порядке, уменьшает счетчик и возвращает True.
    В противном случае возвращает False.
    """
    async with db_pool.acquire() as conn:
        # Получаем подписку или создаем бесплатную, если ее нет
        subscription = await conn.fetchrow(
            """
            INSERT INTO subscriptions (user_id) VALUES ($1)
            ON CONFLICT (user_id) DO NOTHING
            RETURNING *;
            """,
            user_id
        )
        if not subscription:
             subscription = await conn.fetchrow("SELECT * FROM subscriptions WHERE user_id = $1", user_id)

        # Проверяем, не истек ли платный тариф
        if subscription['plan_name'] != 'free' and subscription['expires_at'] < datetime.datetime.now(datetime.timezone.utc):
            # Тариф истек, откатываем до бесплатного
            await conn.execute(
                "UPDATE subscriptions SET plan_name = 'free', generations_left = 3, expires_at = NULL WHERE user_id = $1",
                user_id
            )
            # Повторно получаем обновленные данные
            subscription = await conn.fetchrow("SELECT * FROM subscriptions WHERE user_id = $1", user_id)

        # Проверяем, остались ли генерации
        if subscription['generations_left'] > 0:
            # Уменьшаем счетчик и возвращаем успех
            await conn.execute("UPDATE subscriptions SET generations_left = generations_left - 1 WHERE user_id = $1", user_id)
            return True
        else:
            # Лимит исчерпан
            return False```

---

### Шаг 6: Обновление клавиатур

#### `bot/keyboards/inline.py` (МОДИФИЦИРОВАН)
Добавляем новую сложную функцию для генерации меню подписки.

```python
# ... (в самом верху файла)
import datetime

# ... (все старые функции get_..._keyboard)

async def get_subscription_keyboard(user_id: int, lang_code: str, db_pool: asyncpg.Pool) -> tuple[InlineKeyboardMarkup, str]:
    """
    Генерирует клавиатуру и текст для меню управления подпиской.
    """
    async with db_pool.acquire() as conn:
        # Вставляем запись о бесплатном тарифе, если пользователя еще нет в таблице
        await conn.execute("INSERT INTO subscriptions (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING", user_id)
        sub = await conn.fetchrow("SELECT * FROM subscriptions WHERE user_id = $1", user_id)

    builder = InlineKeyboardBuilder()
    
    # Формируем текстовое описание текущего тарифа
    current_plan_name_local = get_text(lang_code, f"plan_{sub['plan_name']}_name")
    text = f"{get_text(lang_code, 'subscription_management_title')}\n\n"
    text += f"<b>{get_text(lang_code, 'your_current_plan', plan_name=current_plan_name_local)}</b>\n"
    text += f"{get_text(lang_code, 'generations_left', count=sub['generations_left'])}\n"
    if sub['expires_at']:
        # Форматируем дату для вывода
        expires_date_str = sub['expires_at'].strftime('%d.%m.%Y')
        text += f"{get_text(lang_code, 'plan_expires_on', date=expires_date_str)}\n"
    
    text += "\n"

    # Добавляем кнопки для улучшения тарифа
    if sub['plan_name'] == 'free':
        text += f"<u>{get_text(lang_code, 'plan_basic_name')}</u>: {get_text(lang_code, 'plan_basic_desc')}\n"
        builder.row(InlineKeyboardButton(text=get_text(lang_code, 'upgrade_to_basic_button'), callback_data="subscribe_basic"))
    
    if sub['plan_name'] in ['free', 'basic']:
        text += f"<u>{get_text(lang_code, 'plan_pro_name')}</u>: {get_text(lang_code, 'plan_pro_desc')}\n"
        builder.row(InlineKeyboardButton(text=get_text(lang_code, 'upgrade_to_pro_button'), callback_data="subscribe_pro"))

    builder.row(InlineKeyboardButton(text=get_text(lang_code, 'back_to_main_menu_button'), callback_data="back_to_main_menu"))
    
    return builder.as_markup(), text