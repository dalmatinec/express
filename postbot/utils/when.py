"""Разбор времени для отложенной публикации.

Понимает: 18:30, 25.10 18:30, 25.10.2026 18:30, завтра 10:00, +30м, +2ч, +1д
Время всегда по часовому поясу из .env.
"""
import re
from datetime import datetime, timedelta, tzinfo


def parse_when(text: str, tz: tzinfo, now: datetime | None = None) -> datetime | None:
    now = now or datetime.now(tz)
    raw = " ".join(text.lower().replace(",", " ").split())

    m = re.fullmatch(r"\+\s*(\d{1,4})\s*(м|мин|ч|час|часа|часов|д|дн|день|дня|дней|m|h|d)?", raw)
    if m:
        amount = int(m[1])
        unit = (m[2] or "м")[0]
        delta = {"м": timedelta(minutes=amount), "m": timedelta(minutes=amount),
                 "ч": timedelta(hours=amount), "h": timedelta(hours=amount)}.get(unit, timedelta(days=amount))
        return now + delta if amount > 0 else None

    day_shift = 0
    for word, shift in (("послезавтра", 2), ("завтра", 1), ("сегодня", 0)):
        if raw.startswith(word):
            day_shift = shift
            raw = raw[len(word):].strip()
            break
    else:
        word = None

    m = re.fullmatch(r"(?:(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\s+)?(\d{1,2})[:.](\d{2})", raw)
    if not m:
        return None
    day, month, year, hour, minute = m.groups()
    hour_i, minute_i = int(hour), int(minute)
    if hour_i > 23 or minute_i > 59:
        return None
    try:
        if day:
            y = int(year) if year else now.year
            if y < 100:
                y += 2000
            when = datetime(y, int(month), int(day), hour_i, minute_i, tzinfo=tz)
            if not year and when < now - timedelta(days=1):
                when = when.replace(year=y + 1)  # 05.01 в декабре - это уже следующий год
        else:
            base = now + timedelta(days=day_shift)
            when = datetime(base.year, base.month, base.day, hour_i, minute_i, tzinfo=tz)
            if word is None and when <= now:
                when += timedelta(days=1)  # просто 10:00, а уже 12:00 - значит завтра
    except ValueError:
        return None
    return when
