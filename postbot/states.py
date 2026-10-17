"""Шаги, на которых бот ждёт от человека сообщение."""
from aiogram.fsm.state import State, StatesGroup


class Edit(StatesGroup):
    content = State()   # первый шаг: текст или медиа нового поста
    text = State()
    media = State()
    title = State()


class Btn(StatesGroup):
    label = State()     # новая кнопка: текст
    value = State()     # новая кнопка: ссылка, текст для копирования или всплывашки
    bulk = State()      # кнопки списком
    edit_label = State()
    edit_value = State()
    icon = State()


class Pub(StatesGroup):
    time = State()


class Hist(StatesGroup):
    rename = State()


class Adm(StatesGroup):
    channel = State()   # подключение канала
    admin = State()     # добавление админа
    restore = State()   # файл бэкапа
    premium = State()   # эмодзи для проверки
