"""
Администраторская панель управления ботом.
"""

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InputFile
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from config import SUPER_ADMIN_ID
from database import db
from utils import get_text, format_datetime_string, is_text_message
from admin_states import (
    GreetingStates, TopStates, AdminStates,
    GroupStates, UserSearchStates
)
from reply_kb import (
    get_back_keyboard, get_cancel_keyboard,
    get_greeting_submenu_keyboard, get_top_submenu_keyboard,
    get_admins_submenu_keyboard, get_group_submenu_keyboard,
    get_stats_submenu_keyboard, get_admin_main_keyboard
)
from inline_kb import get_user_history_button

router = Router()


# ==================== ГЛАВНОЕ МЕНЮ ====================

@router.message(Command("admin"))
async def admin_command(message: Message, is_admin: bool, is_super_admin: bool):
    """Команда /admin - вход в админ-панель."""
    if not is_admin and not is_super_admin:
        await message.answer(get_text('admin_no_access'))
        return

    await message.answer(
        text=f"{get_text('welcome_to_admin')}\n{get_text('admin_main_menu')}",
        reply_markup=get_admin_main_keyboard()
    )


# ==================== ПРИВЕТСТВЕННОЕ СООБЩЕНИЕ ====================

@router.message(F.text == "👋 Приветственное сообщение")
async def greeting_section(message: Message, state: FSMContext, is_admin: bool, is_super_admin: bool):
    """Раздел приветственного сообщения."""
    if not is_admin and not is_super_admin:
        return

    await message.answer(
        text=get_text('greeting_submenu'),
        reply_markup=get_greeting_submenu_keyboard()
    )


@router.message(F.text == "✏️ Изменить текст")
async def greeting_edit_text(message: Message, state: FSMContext, is_admin: bool, is_super_admin: bool):
    """Редактирование текста приветствия."""
    if not is_admin and not is_super_admin:
        return

    await state.set_state(GreetingStates.waiting_for_text)
    await message.answer(
        text=get_text('greeting_edit_text'),
        reply_markup=get_cancel_keyboard()
    )


@router.message(GreetingStates.waiting_for_text)
async def save_greeting_text(message: Message, state: FSMContext):
    """Сохранить текст приветствия."""
    if message.text == f"{get_text('cancel_button')}":
        await state.clear()
        await message.answer("❌ Отмена.", reply_markup=get_greeting_submenu_keyboard())
        return

    if not is_text_message(message):
        await message.answer(get_text('only_text'))
        return

    # Сохранить в БД
    db.set_setting('start_message', message.text)

    await state.clear()
    await message.answer(
        text=get_text('greeting_text_saved'),
        reply_markup=get_greeting_submenu_keyboard()
    )


@router.message(F.text == "🖼 Изменить изображение")
async def greeting_edit_photo(message: Message, state: FSMContext, is_admin: bool, is_super_admin: bool):
    """Редактирование изображения приветствия."""
    if not is_admin and not is_super_admin:
        return

    await state.set_state(GreetingStates.waiting_for_photo)
    await message.answer(
        text=get_text('greeting_edit_photo'),
        reply_markup=get_cancel_keyboard()
    )


@router.message(GreetingStates.waiting_for_photo)
async def save_greeting_photo(message: Message, state: FSMContext):
    """Сохранить изображение приветствия."""
    if message.text == f"{get_text('cancel_button')}":
        await state.clear()
        await message.answer("❌ Отмена.", reply_markup=get_greeting_submenu_keyboard())
        return

    if not message.photo:
        await message.answer("⚠️ Отправьте фотографию.")
        return

    # Получить самое большое фото
    photo = message.photo[-1]
    file_id = photo.file_id

    # Сохранить в БД
    setting = db.get_setting('start_message')
    text = setting['value'] if setting else None
    db.set_setting('start_message', text, photo_file_id=file_id)

    await state.clear()
    await message.answer(
        text=get_text('greeting_photo_saved'),
        reply_markup=get_greeting_submenu_keyboard()
    )


@router.message(F.text == "❌ Удалить изображение")
async def delete_greeting_photo(message: Message, is_admin: bool, is_super_admin: bool):
    """Удалить изображение приветствия."""
    if not is_admin and not is_super_admin:
        return

    # Сохранить в БД без фото
    setting = db.get_setting('start_message')
    text = setting['value'] if setting else None
    db.set_setting('start_message', text, photo_file_id=None)

    await message.answer(
        text=get_text('greeting_photo_deleted'),
        reply_markup=get_greeting_submenu_keyboard()
    )


@router.message(F.text == "👁 Просмотреть текущее сообщение")
async def preview_greeting(message: Message, is_admin: bool, is_super_admin: bool):
    """Просмотреть текущее приветствие."""
    if not is_admin and not is_super_admin:
        return

    setting = db.get_setting('start_message')

    if not setting or not setting.get('value'):
        await message.answer(
            text=f"{get_text('greeting_preview')}\n\n(не установлено)",
            reply_markup=get_greeting_submenu_keyboard()
        )
        return

    text = setting['value'].format(first_name="[Имя]", last_name="[Фамилия]")
    photo_file_id = setting.get('photo_file_id')

    if photo_file_id:
        await message.answer_photo(
            photo=photo_file_id,
            caption=f"{get_text('greeting_preview')}\n\n{text}",
            reply_markup=get_greeting_submenu_keyboard()
        )
    else:
        await message.answer(
            text=f"{get_text('greeting_preview')}\n\n{text}",
            reply_markup=get_greeting_submenu_keyboard()
        )


# ==================== ТОП НЕДЕЛИ ====================

@router.message(F.text == "🏆 Топ недели")
async def top_section(message: Message, is_admin: bool, is_super_admin: bool):
    """Раздел топа недели."""
    if not is_admin and not is_super_admin:
        return

    await message.answer(
        text=get_text('top_submenu'),
        reply_markup=get_top_submenu_keyboard()
    )


@router.message(F.text == "✏️ Изменить сообщение")
async def top_edit_message(message: Message, state: FSMContext, is_admin: bool, is_super_admin: bool):
    """Редактирование сообщения топа недели."""
    if not is_admin and not is_super_admin:
        return

    await state.set_state(TopStates.waiting_for_message)
    await message.answer(
        text=get_text('top_edit'),
        reply_markup=get_cancel_keyboard()
    )


@router.message(TopStates.waiting_for_message)
async def save_top_message(message: Message, state: FSMContext):
    """Сохранить сообщение топа недели."""
    if message.text == f"{get_text('cancel_button')}":
        await state.clear()
        await message.answer("❌ Отмена.", reply_markup=get_top_submenu_keyboard())
        return

    if not is_text_message(message):
        await message.answer(get_text('only_text'))
        return

    # Сохранить в БД
    db.set_setting('top_message', message.text)

    await state.clear()
    await message.answer(
        text=get_text('top_saved'),
        reply_markup=get_top_submenu_keyboard()
    )


@router.message(F.text == "👁 Просмотреть сообщение")
async def preview_top(message: Message, is_admin: bool, is_super_admin: bool):
    """Просмотреть текущий топ недели."""
    if not is_admin and not is_super_admin:
        return

    setting = db.get_setting('top_message')

    if setting and setting.get('value'):
        text = setting['value']
    else:
        text = get_text('top_default')

    await message.answer(
        text=f"{get_text('top_preview')}\n\n{text}",
        reply_markup=get_top_submenu_keyboard()
    )


# ==================== АДМИНИСТРАТОРЫ ====================

@router.message(F.text == "👮 Администраторы")
async def admins_section(message: Message, is_admin: bool, is_super_admin: bool):
    """Раздел администраторов."""
    if not is_admin and not is_super_admin:
        return

    await message.answer(
        text=get_text('admins_submenu'),
        reply_markup=get_admins_submenu_keyboard()
    )


@router.message(F.text == "➕ Добавить администратора")
async def admin_add(message: Message, state: FSMContext, is_admin: bool, is_super_admin: bool):
    """Добавить администратора."""
    if not is_admin and not is_super_admin:
        return

    await state.set_state(AdminStates.waiting_for_admin_id_add)
    await message.answer(
        text=get_text('admins_add'),
        reply_markup=get_cancel_keyboard()
    )


@router.message(AdminStates.waiting_for_admin_id_add)
async def save_new_admin(message: Message, state: FSMContext):
    """Сохранить нового администратора."""
    if message.text == f"{get_text('cancel_button')}":
        await state.clear()
        await message.answer("❌ Отмена.", reply_markup=get_admins_submenu_keyboard())
        return

    try:
        admin_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введите корректный User ID.")
        return

    if db.add_admin(admin_id):
        await state.clear()
        await message.answer(
            text=get_text('admins_added'),
            reply_markup=get_admins_submenu_keyboard()
        )
    else:
        await message.answer("⚠️ Администратор уже добавлен.")


@router.message(F.text == "➖ Удалить администратора")
async def admin_remove(message: Message, state: FSMContext, is_admin: bool, is_super_admin: bool):
    """Удалить администратора."""
    if not is_admin and not is_super_admin:
        return

    await state.set_state(AdminStates.waiting_for_admin_id_remove)
    await message.answer(
        text=get_text('admins_remove'),
        reply_markup=get_cancel_keyboard()
    )


@router.message(AdminStates.waiting_for_admin_id_remove)
async def delete_admin(message: Message, state: FSMContext):
    """Удалить администратора."""
    if message.text == f"{get_text('cancel_button')}":
        await state.clear()
        await message.answer("❌ Отмена.", reply_markup=get_admins_submenu_keyboard())
        return

    try:
        admin_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введите корректный User ID.")
        return

    if db.remove_admin(admin_id):
        await state.clear()
        await message.answer(
            text=get_text('admins_removed'),
            reply_markup=get_admins_submenu_keyboard()
        )
    else:
        await message.answer("⚠️ Не удалось удалить администратора.")


@router.message(F.text == "📋 Список администраторов")
async def list_admins(message: Message, is_admin: bool, is_super_admin: bool):
    """Показать список администраторов."""
    if not is_admin and not is_super_admin:
        return

    admins = db.get_all_admins()

    if not admins:
        await message.answer(
            text=f"{get_text('admins_list')}\n\n(нет администраторов)",
            reply_markup=get_admins_submenu_keyboard()
        )
        return

    text = f"{get_text('admins_list')}\n\n"

    # Добавить супер-админа
    text += f"{SUPER_ADMIN_ID} {get_text('admins_super')}\n"

    # Добавить остальных
    for admin in admins:
        if admin['is_super_admin'] == 0:
            text += f"{admin['admin_id']} {get_text('admins_regular')}\n"

    await message.answer(
        text=text,
        reply_markup=get_admins_submenu_keyboard()
    )


# ==================== РАБОЧАЯ ГРУППА ====================

@router.message(F.text == "👥 Рабочая группа")
async def group_section(message: Message, is_admin: bool, is_super_admin: bool):
    """Раздел рабочей группы."""
    if not is_admin and not is_super_admin:
        return

    await message.answer(
        text=get_text('group_submenu'),
        reply_markup=get_group_submenu_keyboard()
    )


@router.message(F.text == "✏️ Изменить ID группы")
async def group_change_id(message: Message, state: FSMContext, is_admin: bool, is_super_admin: bool):
    """Изменить ID группы."""
    if not is_admin and not is_super_admin:
        return

    await state.set_state(GroupStates.waiting_for_group_id)
    await message.answer(
        text=get_text('group_change_id'),
        reply_markup=get_cancel_keyboard()
    )


@router.message(GroupStates.waiting_for_group_id)
async def save_group_id(message: Message, state: FSMContext):
    """Сохранить ID группы."""
    if message.text == f"{get_text('cancel_button')}":
        await state.clear()
        await message.answer("❌ Отмена.", reply_markup=get_group_submenu_keyboard())
        return

    try:
        group_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введите корректный ID группы.")
        return

    # Сохранить в БД
    db.set_setting('group_id', str(group_id))

    await state.clear()
    await message.answer(
        text=get_text('group_id_saved'),
        reply_markup=get_group_submenu_keyboard()
    )


@router.message(F.text == "👁 Посмотреть текущий ID")
async def show_group_id(message: Message, is_admin: bool, is_super_admin: bool):
    """Показать текущий ID группы."""
    if not is_admin and not is_super_admin:
        return

    setting = db.get_setting('group_id')
    group_id = setting['value'] if setting and setting.get('value') else "не установлен"

    await message.answer(
        text=get_text('group_current_id').format(group_id=group_id),
        reply_markup=get_group_submenu_keyboard()
    )


@router.message(F.text == "✅ Проверить подключение")
async def check_group_connection(message: Message, is_admin: bool, is_super_admin: bool):
    """Проверить подключение к группе."""
    if not is_admin and not is_super_admin:
        return

    await message.answer(get_text('group_check_connection'))

    setting = db.get_setting('group_id')
    if not setting or not setting.get('value'):
        await message.answer(
            text=get_text('group_disconnected'),
            reply_markup=get_group_submenu_keyboard()
        )
        return

    try:
        group_id = int(setting['value'])
        # Попытаться получить информацию о группе
        chat = await message.bot.get_chat(group_id)
        await message.answer(
            text=get_text('group_connected'),
            reply_markup=get_group_submenu_keyboard()
        )
    except Exception as e:
        await message.answer(
            text=get_text('group_disconnected'),
            reply_markup=get_group_submenu_keyboard()
        )


# ==================== ИНФОРМАЦИЯ О ПОЛЬЗОВАТЕЛЕ ====================

@router.message(F.text == "🔎 Информация о пользователе")
async def user_info_section(message: Message, state: FSMContext, is_admin: bool, is_super_admin: bool):
    """Раздел поиска информации о пользователе."""
    if not is_admin and not is_super_admin:
        return

    await state.set_state(UserSearchStates.waiting_for_user_id)
    await message.answer(
        text=get_text('user_info_prompt'),
        reply_markup=get_cancel_keyboard()
    )


@router.message(UserSearchStates.waiting_for_user_id)
async def show_user_info(message: Message, state: FSMContext):
    """Показать информацию о пользователе."""
    if message.text == f"{get_text('cancel_button')}":
        await state.clear()
        await message.answer("❌ Отмена.", reply_markup=get_admin_main_keyboard())
        return

    try:
        user_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введите корректный User ID.")
        return

    user = db.get_user(user_id)

    if not user:
        await message.answer(
            text=get_text('user_info_not_found'),
            reply_markup=get_cancel_keyboard()
        )
        return

    # Форматировать информацию
    text = f"{get_text('user_info_found')}\n\n"
    text += f"{get_text('user_info_id')} {user['user_id']}\n"
    text += f"{get_text('user_info_name')} {user['first_name']} {user['last_name']}\n"

    if user['username']:
        text += f"{get_text('user_info_username')} @{user['username']}\n"

    text += f"{get_text('user_info_first_contact')} {user['first_contact_date']}\n"
    text += f"{get_text('user_info_orders_count')} {user['order_count']}\n"

    status = get_text('user_info_status_active') if user['is_blocked'] == 0 else get_text('user_info_status_blocked')
    text += f"{get_text('user_info_status')} {status}"

    await state.clear()
    await message.answer(
        text=text,
        reply_markup=get_user_history_button(user_id)
    )


# ==================== ИСТОРИЯ ПОЛЬЗОВАТЕЛЯ ====================

@router.callback_query(F.data.startswith("admin_user_history_"))
async def show_user_history(callback: CallbackQuery):
    """Показать историю обращений пользователя."""
    user_id = int(callback.data.replace("admin_user_history_", ""))

    orders = db.get_user_orders_history(user_id)

    if not orders:
        await callback.message.edit_text(
            text=f"{get_text('user_history_title')}\n\nUser ID: {user_id}\n\n{get_text('user_history_empty')}"
        )
        return

    text = f"{get_text('user_history_title')}\n\nUser ID: {user_id}\n\n"

    for idx, order in enumerate(orders, 1):
        text += f"№{idx}\n{order['order_date']}\n\n{order['message_text']}\n\n"
        text += f"{get_text('separator')}\n\n"

    await callback.message.edit_text(text=text)


# ==================== СТАТИСТИКА ====================

@router.message(F.text == "📊 Статистика")
async def stats_section(message: Message, is_admin: bool, is_super_admin: bool):
    """Раздел статистики."""
    if not is_admin and not is_super_admin:
        return

    await message.answer(
        text="Выберите тип статистики:",
        reply_markup=get_stats_submenu_keyboard()
    )


@router.message(F.text == "👥 Пользователи")
async def show_users_stat(message: Message, is_admin: bool, is_super_admin: bool):
    """Показать кол-во пользователей."""
    if not is_admin and not is_super_admin:
        return

    stats = db.get_stats()
    await message.answer(
        text=f"{get_text('stats_users')} {stats['total_users']}",
        reply_markup=get_stats_submenu_keyboard()
    )


@router.message(F.text == "📨 Обращения")
async def show_orders_stat(message: Message, is_admin: bool, is_super_admin: bool):
    """Показать кол-во обращений."""
    if not is_admin and not is_super_admin:
        return

    stats = db.get_stats()
    await message.answer(
        text=f"{get_text('stats_orders')} {stats['total_orders']}",
        reply_markup=get_stats_submenu_keyboard()
    )


@router.message(F.text == "🚫 Заблокированные")
async def show_blocked_stat(message: Message, is_admin: bool, is_super_admin: bool):
    """Показать кол-во заблокированных."""
    if not is_admin and not is_super_admin:
        return

    stats = db.get_stats()
    await message.answer(
        text=f"{get_text('stats_blocked')} {stats['blocked_users']}",
        reply_markup=get_stats_submenu_keyboard()
    )


@router.message(F.text == "👮 Администраторы")
async def show_admins_stat(message: Message, is_admin: bool, is_super_admin: bool):
    """Показать кол-во администраторов."""
    if not is_admin and not is_super_admin:
        return

    stats = db.get_stats()
    await message.answer(
        text=f"{get_text('stats_admins')} {stats['total_admins'] + 1}",  # +1 за супер-админа
        reply_markup=get_stats_submenu_keyboard()
    )


# ==================== КНОПКА НАЗАД ====================

@router.message(F.text.endswith("⬅️ Назад"))
async def back_button(message: Message, state: FSMContext, is_admin: bool, is_super_admin: bool):
    """Обработка кнопки назад."""
    if not is_admin and not is_super_admin:
        return

    await state.clear()

    # Определить где находимся и вернуться в главное меню
    await message.answer(
        text=f"{get_text('welcome_to_admin')}\n{get_text('admin_main_menu')}",
        reply_markup=get_admin_main_keyboard()
    )


@router.message(F.text == "👁 Просмотреть текущее сообщение")
async def preview_greeting_handler(message: Message, is_admin: bool, is_super_admin: bool):
    """Просмотреть приветствие (для очереди обработки)."""
    await preview_greeting(message, is_admin, is_super_admin)


@router.message(F.text == "👁 Просмотреть сообщение")
async def preview_top_handler(message: Message, is_admin: bool, is_super_admin: bool):
    """Просмотреть топ (для очереди обработки)."""
    await preview_top(message, is_admin, is_super_admin)
