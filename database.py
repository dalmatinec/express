"""
Работа с SQLite базой данных.
Все операции с БД происходят здесь.
"""

import sqlite3
from typing import Optional, List

from config import DATABASE_PATH


class Database:
    def __init__(self, db_path: str = DATABASE_PATH):
        self.db_path = db_path
        self.init_db()

    def get_connection(self):
        """Получить подключение к БД."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def execute(self, query: str, params: tuple = ()) -> sqlite3.Cursor:
        """Выполнить запрос на запись."""
        conn = self.get_connection()
        try:
            with conn:
                return conn.execute(query, params)
        finally:
            conn.close()

    def fetchone(self, query: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        conn = self.get_connection()
        try:
            return conn.execute(query, params).fetchone()
        finally:
            conn.close()

    def fetchall(self, query: str, params: tuple = ()) -> List[sqlite3.Row]:
        conn = self.get_connection()
        try:
            return conn.execute(query, params).fetchall()
        finally:
            conn.close()

    def init_db(self):
        """Инициализация таблиц БД."""
        conn = self.get_connection()
        with conn:
            # Пользователи, которые писали боту
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    first_name TEXT,
                    last_name TEXT,
                    username TEXT,
                    is_banned INTEGER DEFAULT 0,
                    first_seen TEXT DEFAULT CURRENT_TIMESTAMP,
                    last_seen TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # Связь "сообщение в группе -> пользователь"
            conn.execute("""
                CREATE TABLE IF NOT EXISTS links (
                    chat_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (chat_id, message_id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS admins (
                    admin_id INTEGER PRIMARY KEY,
                    name TEXT,
                    username TEXT
                )
            """)
            # Настройки (group_id и т.п.)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            # Счётчики для статистики (например, количество ответов)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS counters (
                    key TEXT PRIMARY KEY,
                    value INTEGER NOT NULL DEFAULT 0
                )
            """)
            # Тексты, изменённые через /settext (перекрывают texts.json)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS texts (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
        conn.close()

    # ==================== ПОЛЬЗОВАТЕЛИ ====================

    def upsert_user(self, user_id: int, first_name: str, last_name: str, username: str):
        self.execute(
            """INSERT INTO users (user_id, first_name, last_name, username) VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                   first_name = excluded.first_name,
                   last_name = excluded.last_name,
                   username = excluded.username,
                   last_seen = CURRENT_TIMESTAMP""",
            (user_id, first_name, last_name, username)
        )

    def get_user(self, user_id: int) -> Optional[dict]:
        row = self.fetchone("SELECT * FROM users WHERE user_id = ?", (user_id,))
        return dict(row) if row else None

    def set_banned(self, user_id: int, banned: bool):
        self.execute(
            # Если человек ещё не писал боту — не считаем его пользователем в статистике
            """INSERT INTO users (user_id, is_banned, first_seen, last_seen) VALUES (?, ?, NULL, NULL)
               ON CONFLICT(user_id) DO UPDATE SET is_banned = excluded.is_banned""",
            (user_id, int(banned))
        )

    def get_banned(self) -> List[dict]:
        return [dict(row) for row in self.fetchall("SELECT * FROM users WHERE is_banned = 1")]

    def is_banned(self, user_id: int) -> bool:
        row = self.fetchone("SELECT is_banned FROM users WHERE user_id = ?", (user_id,))
        return bool(row and row["is_banned"])

    # ==================== СВЯЗИ СООБЩЕНИЙ ====================

    def save_link(self, chat_id: int, message_id: int, user_id: int):
        self.execute(
            "INSERT OR REPLACE INTO links (chat_id, message_id, user_id) VALUES (?, ?, ?)",
            (chat_id, message_id, user_id)
        )

    def get_linked_user(self, chat_id: int, message_id: int) -> Optional[int]:
        row = self.fetchone(
            "SELECT user_id FROM links WHERE chat_id = ? AND message_id = ?",
            (chat_id, message_id)
        )
        return row["user_id"] if row else None

    # ==================== АДМИНИСТРАТОРЫ ====================

    def add_admin(self, admin_id: int, name: str, username: str) -> bool:
        """Добавить админа (или обновить имя). True — если админ новый."""
        is_new = not self.is_admin(admin_id)
        self.execute(
            """INSERT INTO admins (admin_id, name, username) VALUES (?, ?, ?)
               ON CONFLICT(admin_id) DO UPDATE SET name = excluded.name, username = excluded.username""",
            (admin_id, name, username)
        )
        return is_new

    def remove_admin(self, admin_id: int) -> bool:
        return self.execute("DELETE FROM admins WHERE admin_id = ?", (admin_id,)).rowcount > 0

    def is_admin(self, user_id: int) -> bool:
        return self.fetchone("SELECT 1 FROM admins WHERE admin_id = ?", (user_id,)) is not None

    def get_admins(self) -> List[dict]:
        return [dict(row) for row in self.fetchall("SELECT * FROM admins ORDER BY name")]

    def get_admin(self, admin_id: int) -> Optional[dict]:
        row = self.fetchone("SELECT * FROM admins WHERE admin_id = ?", (admin_id,))
        return dict(row) if row else None

    # ==================== НАСТРОЙКИ ====================

    def set_setting(self, key: str, value: Optional[str]):
        self.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))

    def get_setting(self, key: str) -> Optional[str]:
        row = self.fetchone("SELECT value FROM settings WHERE key = ?", (key,))
        return row["value"] if row else None

    # ==================== ТЕКСТЫ ====================

    def set_text(self, key: str, value: str):
        self.execute("INSERT OR REPLACE INTO texts (key, value) VALUES (?, ?)", (key, value))

    def reset_text(self, key: str) -> bool:
        return self.execute("DELETE FROM texts WHERE key = ?", (key,)).rowcount > 0

    def get_text(self, key: str) -> Optional[str]:
        row = self.fetchone("SELECT value FROM texts WHERE key = ?", (key,))
        return row["value"] if row else None

    # ==================== СТАТИСТИКА ====================

    def increment(self, key: str):
        self.execute(
            "INSERT INTO counters (key, value) VALUES (?, 1) ON CONFLICT(key) DO UPDATE SET value = value + 1",
            (key,)
        )

    def get_stats(self) -> dict:
        def count(query: str) -> int:
            return self.fetchone(query)[0]

        def since(days: int) -> str:
            return f"datetime('now', '-{days} days')"

        replies = self.fetchone("SELECT value FROM counters WHERE key = 'replies'")
        return {
            "users": count("SELECT COUNT(*) FROM users WHERE first_seen IS NOT NULL"),
            "new_1": count(f"SELECT COUNT(*) FROM users WHERE first_seen >= {since(1)}"),
            "new_7": count(f"SELECT COUNT(*) FROM users WHERE first_seen >= {since(7)}"),
            "active_1": count(f"SELECT COUNT(*) FROM users WHERE last_seen >= {since(1)}"),
            "active_7": count(f"SELECT COUNT(*) FROM users WHERE last_seen >= {since(7)}"),
            "active_30": count(f"SELECT COUNT(*) FROM users WHERE last_seen >= {since(30)}"),
            "banned": count("SELECT COUNT(*) FROM users WHERE is_banned = 1"),
            "admins": count("SELECT COUNT(*) FROM admins") + 1,  # + супер-админ
            "messages": count("SELECT COUNT(*) FROM links"),
            "messages_1": count(f"SELECT COUNT(*) FROM links WHERE created_at >= {since(1)}"),
            "replies": replies["value"] if replies else 0,
        }


# Глобальный экземпляр БД
db = Database()
