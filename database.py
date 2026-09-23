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
                    is_banned INTEGER DEFAULT 0
                )
            """)
            # Связь "сообщение в группе -> пользователь"
            conn.execute("""
                CREATE TABLE IF NOT EXISTS links (
                    chat_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    PRIMARY KEY (chat_id, message_id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS admins (
                    admin_id INTEGER PRIMARY KEY
                )
            """)
            # Настройки (group_id и т.п.)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
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
                   username = excluded.username""",
            (user_id, first_name, last_name, username)
        )

    def get_user(self, user_id: int) -> Optional[dict]:
        row = self.fetchone("SELECT * FROM users WHERE user_id = ?", (user_id,))
        return dict(row) if row else None

    def set_banned(self, user_id: int, banned: bool):
        self.execute(
            """INSERT INTO users (user_id, is_banned) VALUES (?, ?)
               ON CONFLICT(user_id) DO UPDATE SET is_banned = excluded.is_banned""",
            (user_id, int(banned))
        )

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

    def add_admin(self, admin_id: int) -> bool:
        return self.execute("INSERT OR IGNORE INTO admins (admin_id) VALUES (?)", (admin_id,)).rowcount > 0

    def remove_admin(self, admin_id: int) -> bool:
        return self.execute("DELETE FROM admins WHERE admin_id = ?", (admin_id,)).rowcount > 0

    def is_admin(self, user_id: int) -> bool:
        return self.fetchone("SELECT 1 FROM admins WHERE admin_id = ?", (user_id,)) is not None

    def get_admins(self) -> List[int]:
        return [row["admin_id"] for row in self.fetchall("SELECT admin_id FROM admins")]

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

    def get_stats(self) -> dict:
        return {
            "users": self.fetchone("SELECT COUNT(*) AS c FROM users")["c"],
            "banned": self.fetchone("SELECT COUNT(*) AS c FROM users WHERE is_banned = 1")["c"],
            "messages": self.fetchone("SELECT COUNT(*) AS c FROM links")["c"],
            "admins": self.fetchone("SELECT COUNT(*) AS c FROM admins")["c"],
        }


# Глобальный экземпляр БД
db = Database()
