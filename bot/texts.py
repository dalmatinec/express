"""Тексты бота по умолчанию.

Весь код берёт текст только по ключу. На этапе 4 поверх этого словаря появятся правки
владельца чата, и менять обработчики для этого не придётся.
"""
from __future__ import annotations

from html import escape
from typing import Any


class Html(str):
    """Готовый HTML, например ссылка на человека: при подстановке не экранируется."""


DEFAULTS: dict[str, str] = {
    "start": "👋 Панель владельца. Пока доступны /status и /ping, настройки появятся на этапе 4.",
    "ping": "🟢 Бот на связи.",
    "added": (
        "👋 Спасибо, что добавили!\n"
        "Чтобы я мог модерировать, дайте мне права администратора: "
        "блокировка участников и удаление сообщений."
    ),
    "promoted": "✅ Права получены, бот готов к работе.",
    "demoted": "⚠️ Меня лишили прав администратора, модерация не работает.",
    "me": (
        "👤 <b>{name}</b> · {id}\n"
        "💬 Сообщений в этом чате: <b>{here}</b>\n"
        "🌐 Всего в чатах с этим ботом: <b>{total}</b> (чатов: {chats})"
    ),
    "me_private": (
        "👤 <b>{name}</b> · {id}\n"
        "🌐 Сообщений в чатах с этим ботом: <b>{total}</b> (чатов: {chats})"
    ),
    "status": (
        "📊 <b>Состояние</b>\n"
        "🤖 Ботов запущено: {running}\n"
        "👥 Активных чатов у этого бота: {chats}\n"
        "⚙️ События в обработке: {in_flight}\n"
        "📝 Ждут записи в базу: {pending}\n"
        "🧠 Память процесса: {rss}\n"
        "🎯 Кэш чатов: {cache_hit}% попаданий, записей {cache_size}\n"
        "⏱ Работает: {uptime}"
    ),

    # Карточки наказаний
    "card_ban": "🚫 <b>Бан</b>",
    "card_mute": "🔇 <b>Мут</b>",
    "card_kick": "👢 <b>Кик</b>",
    "card_warn": "⚠️ <b>Предупреждение {count}/{limit}</b> {bar}",
    "card_target": "👤 {target} · {id}",
    "card_forever": "⏳ Навсегда",
    "card_term": "⏳ {term} · до {until}",
    "card_reason": "📝 <i>{reason}</i>",
    "card_admin": "👮 {admin}",
    "card_auto": "🤖 Автоматически",
    "card_escalated_ban": "🔺 <b>Лимит предупреждений:</b> бан",
    "card_escalated_mute": "🔺 <b>Лимит предупреждений:</b> мут",
    "card_escalated_kick": "🔺 <b>Лимит предупреждений:</b> кик",
    "card_lifted_ban": "✅ <b>Бан снят</b>",
    "card_lifted_mute": "✅ <b>Мут снят</b>",
    "card_lifted_warn": "✅ <b>Предупреждение снято</b>",
    "btn_undo": "🔓 Снять",
    "btn_confirm_anon": "✅ Это я, выполнить",
    "anon_prompt": "Вы пишете как анонимный админ. Нажмите кнопку, чтобы я проверил ваши права.",
    "done": "Готово",

    # Просмотр
    "warns": "⚠️ <b>Предупреждения {count}/{limit}</b> {bar}\n👤 {target} · {id}",
    "history_title": "📋 <b>История</b>\n👤 {target} · {id}",
    "journal_title": "📋 <b>Журнал чата</b>",
    "history_empty": "Записей нет.",
    "history_line": "{icon} {details}",
    "journal_line": "{icon} {target} · {id}\n{details}",
    "list_details": "{date} · {term} · {reason} · {admin}",
    "kind_ban": "🚫",
    "kind_mute": "🔇",
    "kind_kick": "👢",
    "kind_warn": "⚠️",
    "kind_revoked": "↩️",
    "no_reason": "без причины",
    "by_system": "бот",
    "term_once": "разово",
    "term_forever": "навсегда",

    # Защита
    "reason_flood": "флуд",
    "reason_links": "ссылка",
    "reason_forward": "репост из канала",
    "reason_mentions": "много упоминаний",
    "reason_stopword": "запрещённое слово",
    "reason_duplicate": "одинаковые сообщения",
    "spam_notice": "🧹 {target}: сообщение удалено, причина: {reason}",
    "protect_no_rights": (
        "⚠️ Защита включена, но у меня нет права удалять сообщения. Выдайте его в настройках чата."
    ),
    "panel_title": "🛡 <b>Защита чата</b>",
    "panel_flood": "⚡️ Антифлуд: {state}",
    "panel_flood_on": "{limit} сообщений за {window} с, затем {action} на {term}",
    "panel_links": "🔗 Ссылки: {state}",
    "panel_links_telegram": "удалять ссылки на Telegram",
    "panel_links_all": "удалять все ссылки",
    "panel_forwards": "↪️ Репосты из каналов: {state}",
    "panel_mentions": "📣 Массовые упоминания: {state}",
    "panel_duplicates": "🔁 Одинаковые сообщения: {state}",
    "panel_action": "⚖️ За спам: {state}",
    "panel_hint": "Админы и люди из белого списка под проверки не попадают.",
    "state_on": "вкл",
    "state_off": "выкл",
    "state_delete": "удалять",
    "state_warn": "удалять и предупреждать",
    "action_mute": "мут",
    "action_ban": "бан",
    "action_kick": "кик",
    "btn_flood": "⚡️ Антифлуд: {state}",
    "btn_links": "🔗 Ссылки: {state}",
    "btn_links_telegram": "Telegram",
    "btn_links_all": "все",
    "btn_forwards": "↪️ Репосты: {state}",
    "btn_mentions": "📣 Упоминания: {state}",
    "btn_duplicates": "🔁 Повторы: {state}",
    "btn_action": "⚖️ {state}",
    "btn_action_warn": "Предупреждать",
    "btn_action_delete": "Только удалять",

    # Белый список
    "whitelist_added": "✅ <b>Добавлен в белый список</b>\n👤 {target} · {id}",
    "whitelist_already": "Этот человек уже в белом списке.",
    "whitelist_removed": "➖ <b>Убран из белого списка</b>\n👤 {target} · {id}",
    "whitelist_missing": "Этого человека нет в белом списке.",
    "whitelist_title": "📃 <b>Белый список</b> · {count}",
    "whitelist_line": "{name} · {id}",
    "whitelist_empty": "Пока никого нет.",

    # Подсказки и ошибки
    "usage_target": (
        "Кого? Ответьте на сообщение человека или укажите @username или ID.\n"
        "Например: <code>/mute @vasya 2ч спам</code>"
    ),
    "mod_err_no_rights": "У вас нет права блокировать участников.",
    "mod_err_bot_no_rights": "Дайте мне право блокировать участников, без него я не смогу.",
    "mod_err_self": "Себя наказать нельзя.",
    "mod_err_target_bot": "Меня наказать нельзя 🙂",
    "mod_err_target_admin": "Администратора наказать нельзя.",
    "mod_err_target_channel": "Это сообщение от имени канала, такое пока не поддерживается.",
    "mod_err_unknown_username": (
        "Я ещё не видел этого человека. Ответьте на его сообщение или укажите ID."
    ),
    "mod_err_user_not_found": "Telegram не нашёл такого пользователя.",
    "mod_err_not_in_chat": "Этого человека нет в чате.",
    "mod_err_too_short": "Минимальный срок 30 секунд.",
    "mod_err_too_long": "Максимальный срок 366 дней. Чтобы навсегда, не указывайте срок.",
    "mod_err_flood": "Telegram просит подождать, попробуйте через минуту.",
    "mod_err_tg_error": "Telegram не выполнил действие, попробуйте ещё раз.",
    "mod_err_no_warns": "Действующих предупреждений нет.",
    "mod_err_already": "Уже снято.",
    "mod_err_not_found": "Запись не найдена.",
    "mod_err_kick_undo": "Кик отменить нельзя: человек может вернуться сам.",
    "mod_err_expired": "Кнопка устарела, отправьте команду ещё раз.",
    "mod_err_bad_button": "Эта кнопка не работает.",
}


def t(key: str, **values: Any) -> str:
    """Подставляет значения, экранируя их для HTML. Сами шаблоны остаются HTML."""
    template = DEFAULTS[key]
    if not values:
        return template
    return template.format(
        **{
            k: escape(v, quote=False) if isinstance(v, str) and not isinstance(v, Html) else v
            for k, v in values.items()
        }
    )
