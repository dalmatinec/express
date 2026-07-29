"""
Состояния (FSM) для администраторской панели.
"""

from aiogram.fsm.state import State, StatesGroup


class GreetingStates(StatesGroup):
    """Состояния для управления приветствием."""
    waiting_for_text = State()
    waiting_for_photo = State()


class TopStates(StatesGroup):
    """Состояния для управления топом недели."""
    waiting_for_message = State()


class AdminStates(StatesGroup):
    """Состояния для управления администраторами."""
    waiting_for_admin_id_add = State()
    waiting_for_admin_id_remove = State()


class GroupStates(StatesGroup):
    """Состояния для управления группой."""
    waiting_for_group_id = State()


class UserSearchStates(StatesGroup):
    """Состояния для поиска пользователя."""
    waiting_for_user_id = State()
    viewing_history = State()
