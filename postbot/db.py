"""База SQLite. Схема меняется только миграциями, новые дописываем в конец списка."""
import logging
from pathlib import Path
from typing import Any, Iterable

import aiosqlite

log = logging.getLogger(__name__)

MIGRATIONS: list[str] = [
    # 1: всё основное
    """
    CREATE TABLE settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    ) WITHOUT ROWID;

    -- медиа лежат файлами в data/media, а file_id у каждого бота свой
    CREATE TABLE media (
        id         INTEGER PRIMARY KEY,
        kind       TEXT NOT NULL,              -- photo video animation document audio
        path       TEXT,                       -- имя файла в data/media, пусто если файл больше 20 МБ
        file_name  TEXT,
        size       INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL
    );
    CREATE TABLE media_file_ids (
        media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
        bot_id   INTEGER NOT NULL,
        file_id  TEXT NOT NULL,
        PRIMARY KEY (media_id, bot_id)
    ) WITHOUT ROWID;

    -- каналы и группы, куда бот может постить
    CREATE TABLE channels (
        id         INTEGER PRIMARY KEY,
        title      TEXT NOT NULL DEFAULT '',
        username   TEXT,
        type       TEXT NOT NULL DEFAULT 'channel',
        can_post   INTEGER NOT NULL DEFAULT 0,
        can_edit   INTEGER NOT NULL DEFAULT 0,
        can_delete INTEGER NOT NULL DEFAULT 0,
        can_pin    INTEGER NOT NULL DEFAULT 0,
        is_present INTEGER NOT NULL DEFAULT 1,
        added_by   INTEGER,
        updated_at INTEGER NOT NULL
    );

    -- админы из бота (владельцы берутся из .env)
    CREATE TABLE admins (
        user_id      INTEGER PRIMARY KEY,
        name         TEXT NOT NULL DEFAULT '',
        all_channels INTEGER NOT NULL DEFAULT 1,
        can_channels INTEGER NOT NULL DEFAULT 0,
        added_by     INTEGER,
        created_at   INTEGER NOT NULL
    );
    CREATE TABLE admin_channels (
        user_id    INTEGER NOT NULL REFERENCES admins(user_id) ON DELETE CASCADE,
        channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
        PRIMARY KEY (user_id, channel_id)
    ) WITHOUT ROWID;

    CREATE TABLE posts (
        id            INTEGER PRIMARY KEY,
        title         TEXT NOT NULL DEFAULT '',
        html          TEXT NOT NULL DEFAULT '',
        media_id      INTEGER REFERENCES media(id) ON DELETE SET NULL,
        silent        INTEGER NOT NULL DEFAULT 0,
        protect       INTEGER NOT NULL DEFAULT 0,
        spoiler       INTEGER NOT NULL DEFAULT 0,
        caption_above INTEGER NOT NULL DEFAULT 0,
        preview       TEXT NOT NULL DEFAULT 'off',   -- off auto small large
        preview_above INTEGER NOT NULL DEFAULT 0,
        pin           INTEGER NOT NULL DEFAULT 0,
        delete_after  INTEGER NOT NULL DEFAULT 0,    -- минуты, 0 значит не удалять
        is_draft      INTEGER NOT NULL DEFAULT 1,
        source_id     INTEGER,                       -- черновик правки поста source_id
        author_id     INTEGER,
        created_at    INTEGER NOT NULL,
        updated_at    INTEGER NOT NULL
    );
    CREATE INDEX ix_posts_list ON posts(is_draft, id);

    CREATE TABLE post_buttons (
        id       INTEGER PRIMARY KEY,
        post_id  INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
        token    TEXT NOT NULL,                      -- для всплывашек, не меняется при правке
        kind     TEXT NOT NULL DEFAULT 'url',        -- url copy popup
        label    TEXT NOT NULL,
        icon     TEXT,
        style    TEXT,
        value    TEXT NOT NULL DEFAULT '',
        row      INTEGER NOT NULL DEFAULT 0,
        position INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX ix_buttons_post ON post_buttons(post_id, row, position);
    CREATE INDEX ix_buttons_token ON post_buttons(token);

    -- куда и когда пост ушёл
    CREATE TABLE publications (
        id         INTEGER PRIMARY KEY,
        post_id    INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
        channel_id INTEGER NOT NULL,
        message_id INTEGER,
        status     TEXT NOT NULL,                    -- scheduled sent deleted failed
        run_at     INTEGER,
        sent_at    INTEGER,
        delete_at  INTEGER,
        error      TEXT,
        by_user    INTEGER,
        created_at INTEGER NOT NULL
    );
    CREATE INDEX ix_pub_post ON publications(post_id);
    CREATE INDEX ix_pub_run ON publications(status, run_at);
    CREATE INDEX ix_pub_delete ON publications(status, delete_at);
    """,
    # 2: индексы под каждую выборку бота, чтобы ни один запрос не перебирал таблицу целиком
    """
    CREATE INDEX ix_posts_media ON posts(media_id);
    CREATE INDEX ix_posts_source ON posts(source_id, is_draft, author_id);
    DROP INDEX ix_pub_post;
    CREATE INDEX ix_pub_post ON publications(post_id, status);
    CREATE INDEX ix_pub_channel ON publications(channel_id, status);
    DROP INDEX ix_pub_run;
    CREATE INDEX ix_pub_run ON publications(run_at) WHERE status = 'scheduled';
    DROP INDEX ix_pub_delete;
    CREATE INDEX ix_pub_delete ON publications(delete_at) WHERE status = 'sent' AND delete_at IS NOT NULL;
    DROP INDEX ix_buttons_token;
    CREATE INDEX ix_buttons_token ON post_buttons(token) WHERE kind = 'popup';
    CREATE INDEX ix_media_created ON media(created_at);
    CREATE INDEX ix_admin_channels_channel ON admin_channels(channel_id);
    CREATE INDEX ix_channels_title ON channels(title COLLATE NOCASE);
    CREATE INDEX ix_pub_sending ON publications(id) WHERE status = 'sending';
    ANALYZE;
    """,
    # 3: было ли медиа в сообщении, когда пост вышел (чтобы правка знала, можно ли поменять текст на медиа)
    """
    ALTER TABLE publications ADD COLUMN has_media INTEGER;
    """,
]


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._conn is not None, "база не подключена"
        return self._conn

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        for pragma in (
            "journal_mode=WAL",       # чтение не ждёт запись
            "synchronous=NORMAL",     # с WAL это надёжно и намного быстрее FULL
            "foreign_keys=ON",
            "busy_timeout=5000",
            "temp_store=MEMORY",
            "cache_size=-16000",      # около 16 МБ кэша страниц
            "mmap_size=67108864",     # 64 МБ базы читаются прямо из памяти
        ):
            await self._conn.execute(f"PRAGMA {pragma}")
        await self._migrate()
        await self._conn.execute("PRAGMA optimize=0x10002")  # обновить статистику для планировщика

    async def close(self) -> None:
        if self._conn is not None:
            try:
                await self._conn.execute("PRAGMA optimize")
            finally:
                await self._conn.close()
                self._conn = None

    async def _migrate(self) -> None:
        version = (await self.fetchval("PRAGMA user_version")) or 0
        for number, sql in enumerate(MIGRATIONS[version:], start=version + 1):
            log.info("Миграция базы %s", number)
            await self.conn.executescript(f"BEGIN;{sql}\nPRAGMA user_version={number};COMMIT;")

    async def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[aiosqlite.Row]:
        async with self.conn.execute(sql, tuple(params)) as cur:
            return list(await cur.fetchall())

    async def fetchone(self, sql: str, params: Iterable[Any] = ()) -> aiosqlite.Row | None:
        async with self.conn.execute(sql, tuple(params)) as cur:
            return await cur.fetchone()

    async def fetchval(self, sql: str, params: Iterable[Any] = ()) -> Any:
        row = await self.fetchone(sql, params)
        return row[0] if row is not None else None

    async def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        """Запрос с коммитом, возвращает lastrowid."""
        cur = await self.conn.execute(sql, tuple(params))
        await self.conn.commit()
        return cur.lastrowid or 0

    async def executemany(self, sql: str, rows: Iterable[Iterable[Any]]) -> None:
        data = [tuple(r) for r in rows]
        if not data:
            return
        await self.conn.executemany(sql, data)
        await self.conn.commit()

    async def backup_to(self, target: Path) -> None:
        """Целая копия базы прямо на ходу."""
        target.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(target) as dst:
            await self.conn.backup(dst)
