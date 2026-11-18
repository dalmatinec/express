#!/usr/bin/env python3
"""Отчёт по ботам на этом сервере: папка, сервис, жив ли токен, ID владельцев, чаты и каналы из базы.
Только чтение: базы копируются во временную папку, токены не печатаются.
Запуск: python3 report.py            (от root, на каждом сервере)
        python3 report.py /opt/mango  (только одна папка)"""
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import urllib.request
from pathlib import Path

TOKEN = re.compile(r"[0-9]{8,11}:[A-Za-z0-9_-]{35}")
OWNER = re.compile(r"(OWNER|ADMIN|SUPER)[A-Za-z_]*(ID|IDS)\s*[=:]\s*(.*)", re.I)
SKIP = {".venv", "venv", "node_modules", ".git", "site-packages", "__pycache__", "backups", "snap"}
ROOTS = os.environ.get("SCAN_DIRS", "/opt /root /home /srv").split()
UNITS = Path(os.environ.get("UNIT_DIR", "/etc/systemd/system"))
CHATLIKE = re.compile(r"chat|group|channel|target|dest|bind|item|button|source|folder", re.I)
SHOWCOL = re.compile(r"(^id$|chat|title|name|username|link|type|owner|kind|enabled|active)", re.I)
HIDECOL = re.compile(r"(token|hash|secret|password|session|phone)", re.I)
MAXROWS = 15


def walk(root: str, depth: int = 3):
    base = root.rstrip("/").count("/")
    for cur, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP]
        if cur.count("/") - base >= depth:
            dirs[:] = []
        yield Path(cur), files


def bot_dirs(only: str | None) -> list[Path]:
    if only:
        return [Path(only)]
    found = set()
    for root in ROOTS:
        if not os.path.isdir(root):
            continue
        for cur, files in walk(root):
            for name in (".env", "config.py"):
                f = cur / name
                if name in files and TOKEN.search(read(f)):
                    found.add(cur)
    return sorted(found)


def read(p: Path) -> str:
    try:
        return p.read_text(errors="ignore")
    except OSError:
        return ""


def token_status(token: str) -> str:
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getMe", timeout=10) as r:
            return "ЖИВОЙ @" + json.load(r)["result"]["username"]
    except urllib.error.HTTPError as e:
        return "МЁРТВЫЙ (Unauthorized)" if e.code == 401 else f"? (код {e.code})"
    except Exception:
        return "? (нет ответа Telegram)"


def service_of(d: Path) -> str:
    if UNITS.is_dir():
        for f in sorted(UNITS.glob("*.service")):
            if re.search(rf"^(WorkingDirectory|ExecStart)=.*{re.escape(str(d))}(/|\s|$)", read(f), re.M):
                return f.stem
    return "-"


def owners(d: Path) -> list[str]:
    out = []
    for name in (".env", "config.py"):
        for line in read(d / name).splitlines():
            m = OWNER.search(line)
            if m and not line.lstrip().startswith("#"):
                out.append(f"{name}: {line.strip()[:90]}")
    return out


def databases(d: Path) -> list[Path]:
    dbs = []
    for cur, files in walk(str(d), 3):
        for f in files:
            if f.endswith((".db", ".sqlite", ".sqlite3")) and "backup" not in str(cur):
                dbs.append(cur / f)
    return dbs


def show_db(path: Path) -> None:
    print(f"   база: {path}  ({path.stat().st_size // 1024} КБ)")
    tmp = Path(tempfile.mkdtemp())
    try:
        for suffix in ("", "-wal", "-shm"):
            src = Path(str(path) + suffix)
            if src.exists():
                shutil.copy2(src, tmp / (path.name + suffix))
        con = sqlite3.connect(str(tmp / path.name))
        con.row_factory = sqlite3.Row
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        counts = {t: con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
        print("      таблицы: " + ", ".join(f"{t}={n}" for t, n in counts.items()))
        for t in tables:
            if not CHATLIKE.search(t) or counts[t] == 0:
                continue
            cols = [c[1] for c in con.execute(f'PRAGMA table_info("{t}")')]
            pick = [c for c in cols if SHOWCOL.search(c) and not HIDECOL.search(c)][:6]
            if not pick:
                continue
            rows = con.execute(f'SELECT {",".join(chr(34) + c + chr(34) for c in pick)} FROM "{t}" LIMIT {MAXROWS}').fetchall()
            print(f"      [{t}] {counts[t]} шт., первые {len(rows)}:")
            for r in rows:
                print("         " + " | ".join(str(v)[:40] for v in tuple(r)))
        con.close()
    except sqlite3.Error as e:
        print(f"      не удалось прочитать: {e}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> None:
    only = sys.argv[1] if len(sys.argv) > 1 else None
    print(f"Сервер: {os.uname().nodename}")
    for d in bot_dirs(only):
        tokens = []
        for name in (".env", "config.py"):
            tokens += TOKEN.findall(read(d / name))
        status = token_status(tokens[0]) if tokens else "токен не найден"
        print("=" * 70)
        print(f"{d}   сервис: {service_of(d)}   токен: {status}" + (f"   бот №{tokens[0].split(':')[0]}" if tokens else ""))
        for line in owners(d) or ["владелец: не найден в .env/config.py"]:
            print(f"   {line}")
        dbs = databases(d)
        for db in dbs:
            show_db(db)
        if not dbs:
            print("   баз данных не найдено")


if __name__ == "__main__":
    main()
