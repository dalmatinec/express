"""
Работа с SQLite базой данных.
Все операции с БД происходят здесь.
"""

import sqlite3
from datetime import datetime
from typing import Optional, List, Tuple
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

    def init_db(self):
        """Инициализация таблиц БД."""
        conn = self.get_connection()
        cursor = conn.cursor()

        # Таблица пользователей
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                first_name TEXT,
                last_name TEXT,
                username TEXT,
                first_contact_date TEXT,
                order_count INTEGER DEFAULT 0,
                is_blocked INTEGER DEFAULT 0
            )
        """)

        # Таблица заявок/заказов
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                order_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                message_text TEXT NOT NULL,
                order_date TEXT NOT NULL,
                message_id_in_group INTEGER,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        """)

        # Таблица администраторов
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                admin_id INTEGER PRIMARY KEY,
                is_super_admin INTEGER DEFAULT 0
            )
        """)

        # Таблица блокировок
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS blocks (
                user_id INTEGER PRIMARY KEY,
                blocked_date TEXT,
                reason TEXT,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        """)

        # Таблица настроек
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                photo_file_id TEXT,
                custom_emoji_id TEXT
            )
        """)

        # Таблица статистики
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS stats (
                stat_key TEXT PRIMARY KEY,
                stat_value INTEGER DEFAULT 0
            )
        """)

        conn.commit()
        conn.close()

        # Инициализация стартовых значений
        self.init_default_settings()

    def init_default_settings(self):
        """Инициализация стандартных значений в settings."""
        conn = self.get_connection()
        cursor = conn.cursor()

        defaults = [
            ("start_message", None),
            ("start_photo_file_id", None),
            ("start_custom_emoji", None),
            ("top_message", None),
            ("group_id", None),
        ]

        for key, _ in defaults:
            cursor.execute("SELECT 1 FROM settings WHERE key = ?", (key,))
            if not cursor.fetchone():
                cursor.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, None))

        conn.commit()
        conn.close()

    # ==================== ПОЛЬЗОВАТЕЛИ ====================

    def add_or_update_user(self, user_id: int, first_name: str, last_name: str, username: str) -> bool:
        """Добавить или обновить пользователя."""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
        exists = cursor.fetchone()

        if exists:
            cursor.execute(
                "UPDATE users SET first_name = ?, last_name = ?, username = ? WHERE user_id = ?",
                (first_name, last_name, username, user_id)
            )
        else:
            now = datetime.now().strftime("%d.%m.%Y • %H:%M")
            cursor.execute(
                """INSERT INTO users (user_id, first_name, last_name, username, first_contact_date, order_count)
                   VALUES (?, ?, ?, ?, ?, 0)""",
                (user_id, first_name, last_name, username, now)
            )

        conn.commit()
        conn.close()
        return True

    def get_user(self, user_id: int) -> Optional[dict]:
        """Получить информацию о пользователе."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        user = cursor.fetchone()
        conn.close()
        return dict(user) if user else None

    def get_all_users(self) -> List[dict]:
        """Получить всех пользователей."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users")
        users = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return users

    # ==================== ЗАКАЗЫ ====================

    def save_order(self, user_id: int, message_text: str, message_id_in_group: int = None) -> int:
        """Сохранить заявку/заказ."""
        conn = self.get_connection()
        cursor = conn.cursor()

        now = datetime.now().strftime("%d.%m.%Y • %H:%M")
        cursor.execute(
            """INSERT INTO orders (user_id, message_text, order_date, message_id_in_group)
               VALUES (?, ?, ?, ?)""",
            (user_id, message_text, now, message_id_in_group)
        )

        # Увеличить счётчик заказов пользователя
        cursor.execute("UPDATE users SET order_count = order_count + 1 WHERE user_id = ?", (user_id,))

        order_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return order_id

    def get_user_orders_history(self, user_id: int) -> List[dict]:
        """Получить историю заказов пользователя."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT order_id, message_text, order_date FROM orders WHERE user_id = ? ORDER BY order_date DESC",
            (user_id,)
        )
        orders = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return orders

    def get_order_by_message_id(self, message_id: int) -> Optional[dict]:
        """Получить заказ по ID сообщения в группе."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM orders WHERE message_id_in_group = ?", (message_id,))
        order = cursor.fetchone()
        conn.close()
        return dict(order) if order else None

    # ==================== АДМИНИСТРАТОРЫ ====================

    def add_admin(self, admin_id: int, is_super: int = 0) -> bool:
        """Добавить администратора."""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT 1 FROM admins WHERE admin_id = ?", (admin_id,))
        if cursor.fetchone():
            conn.close()
            return False

        cursor.execute("INSERT INTO admins (admin_id, is_super_admin) VALUES (?, ?)", (admin_id, is_super))
        conn.commit()
        conn.close()
        return True

    def remove_admin(self, admin_id: int) -> bool:
        """Удалить администратора."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM admins WHERE admin_id = ? AND is_super_admin = 0", (admin_id,))
        conn.commit()
        deleted = cursor.rowcount > 0
        conn.close()
        return deleted

    def is_admin(self, user_id: int, super_admin_id: int) -> bool:
        """Проверить, является ли пользователь администратором."""
        if user_id == super_admin_id:
            return True

        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM admins WHERE admin_id = ?", (user_id,))
        is_admin = cursor.fetchone() is not None
        conn.close()
        return is_admin

    def get_all_admins(self) -> List[dict]:
        """Получить всех администраторов."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM admins ORDER BY is_super_admin DESC")
        admins = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return admins

    # ==================== БЛОКИРОВКИ ====================

    def block_user(self, user_id: int, reason: str = None) -> bool:
        """Заблокировать пользователя."""
        conn = self.get_connection()
        cursor = conn.cursor()

        now = datetime.now().strftime("%d.%m.%Y • %H:%M")
        cursor.execute(
            "INSERT OR IGNORE INTO blocks (user_id, blocked_date, reason) VALUES (?, ?, ?)",
            (user_id, now, reason)
        )
        cursor.execute("UPDATE users SET is_blocked = 1 WHERE user_id = ?", (user_id,))

        conn.commit()
        conn.close()
        return True

    def unblock_user(self, user_id: int) -> bool:
        """Разблокировать пользователя."""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute("DELETE FROM blocks WHERE user_id = ?", (user_id,))
        cursor.execute("UPDATE users SET is_blocked = 0 WHERE user_id = ?", (user_id,))

        conn.commit()
        conn.close()
        return True

    def is_user_blocked(self, user_id: int) -> bool:
        """Проверить, заблокирован ли пользователь."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM blocks WHERE user_id = ?", (user_id,))
        is_blocked = cursor.fetchone() is not None
        conn.close()
        return is_blocked

    def get_blocked_users(self) -> List[dict]:
        """Получить всех заблокированных пользователей."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM blocks")
        users = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return users

    # ==================== НАСТРОЙКИ ====================

    def set_setting(self, key: str, value: str, photo_file_id: str = None, custom_emoji_id: str = None):
        """Установить значение настройки."""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute(
            "UPDATE settings SET value = ?, photo_file_id = ?, custom_emoji_id = ? WHERE key = ?",
            (value, photo_file_id, custom_emoji_id, key)
        )

        conn.commit()
        conn.close()

    def get_setting(self, key: str) -> Optional[dict]:
        """Получить значение настройки."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT value, photo_file_id, custom_emoji_id FROM settings WHERE key = ?", (key,))
        result = cursor.fetchone()
        conn.close()
        return dict(result) if result else None

    def get_all_settings(self) -> dict:
        """Получить все настройки."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT key, value, photo_file_id, custom_emoji_id FROM settings")
        settings = {row['key']: {
            'value': row['value'],
            'photo_file_id': row['photo_file_id'],
            'custom_emoji_id': row['custom_emoji_id']
        } for row in cursor.fetchall()}
        conn.close()
        return settings

    # ==================== СТАТИСТИКА ====================

    def get_stats(self) -> dict:
        """Получить статистику."""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) as count FROM users")
        total_users = cursor.fetchone()['count']

        cursor.execute("SELECT COUNT(*) as count FROM orders")
        total_orders = cursor.fetchone()['count']

        cursor.execute("SELECT COUNT(*) as count FROM blocks")
        blocked_users = cursor.fetchone()['count']

        cursor.execute("SELECT COUNT(*) as count FROM admins")
        total_admins = cursor.fetchone()['count']

        conn.close()

        return {
            'total_users': total_users,
            'total_orders': total_orders,
            'blocked_users': blocked_users,
            'total_admins': total_admins
        }


# Глобальный экземпляр БД
db = Database()
