"""Тесты мелких функций без Telegram."""
import unittest
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram.types import Message

from postbot.utils.layout import grid_of, move
from postbot.utils.text import normalize_url, parse_button_list, parse_label
from postbot.utils.when import parse_when

TZ = ZoneInfo("Asia/Almaty")
NOW = datetime(2026, 10, 17, 16, 0, tzinfo=TZ)


def msg(text: str, entities: list | None = None) -> Message:
    return Message.model_validate({"message_id": 1, "date": 0, "chat": {"id": 1, "type": "private"},
                                   "text": text, "entities": entities or []})


class LayoutTests(unittest.TestCase):
    def test_up_joins_row_above_up_to_three(self) -> None:
        grid = [[1, 2], [3]]
        grid = move(grid, 3, "up")
        self.assertEqual(grid, [[1, 2, 3]])
        grid = move(grid, 3, "up")  # в самом верхнем ряду: отдельным рядом выше
        self.assertEqual(grid, [[3], [1, 2]])
        self.assertEqual(move(grid, 3, "up"), [[3], [1, 2]])  # выше некуда

    def test_full_row_above(self) -> None:
        grid = [[1, 2, 3], [4, 5]]
        self.assertEqual(move(grid, 5, "up"), [[1, 2, 3], [5], [4]])
        self.assertEqual(move([[1, 2, 3], [4]], 4, "up"), [[4], [1, 2, 3]])  # одна в ряду: меняются рядами

    def test_down_sides_solo(self) -> None:
        self.assertEqual(move([[1], [2, 3]], 1, "down"), [[1, 2, 3]])
        self.assertEqual(move([[1, 2]], 1, "down"), [[2], [1]])
        self.assertEqual(move([[1], [2, 3, 4]], 1, "down"), [[2, 3, 4], [1]])
        self.assertEqual(move([[1, 2, 3]], 3, "left"), [[1, 3, 2]])
        self.assertEqual(move([[1, 2]], 2, "right"), [[1, 2]])
        self.assertEqual(move([[1, 2, 3]], 1, "solo"), [[2, 3], [1]])
        self.assertEqual(move([[1]], 1, "solo"), [[1]])

    def test_grid_of(self) -> None:
        @dataclass
        class B:
            id: int
            row: int
        self.assertEqual(grid_of([B(1, 0), B(2, 0), B(3, 2)]), [[1, 2], [3]])


class WhenTests(unittest.TestCase):
    def test_formats(self) -> None:
        self.assertEqual(parse_when("18:30", TZ, NOW), datetime(2026, 10, 17, 18, 30, tzinfo=TZ))
        self.assertEqual(parse_when("10:00", TZ, NOW), datetime(2026, 10, 18, 10, 0, tzinfo=TZ))
        self.assertEqual(parse_when("завтра 9:05", TZ, NOW), datetime(2026, 10, 18, 9, 5, tzinfo=TZ))
        self.assertEqual(parse_when("25.10 12:00", TZ, NOW), datetime(2026, 10, 25, 12, 0, tzinfo=TZ))
        self.assertEqual(parse_when("05.01 12:00", TZ, NOW), datetime(2027, 1, 5, 12, 0, tzinfo=TZ))
        self.assertEqual(parse_when("+2ч", TZ, NOW), datetime(2026, 10, 17, 18, 0, tzinfo=TZ))
        self.assertEqual(parse_when("+30м", TZ, NOW), datetime(2026, 10, 17, 16, 30, tzinfo=TZ))
        self.assertIsNone(parse_when("25:00", TZ, NOW))
        self.assertIsNone(parse_when("когда-нибудь", TZ, NOW))


class TextTests(unittest.TestCase):
    def test_urls(self) -> None:
        self.assertEqual(normalize_url("@chan_name"), "https://t.me/chan_name")
        self.assertEqual(normalize_url("t.me/x"), "https://t.me/x")
        self.assertEqual(normalize_url("site.com/a"), "https://site.com/a")
        self.assertIsNone(normalize_url("просто текст"))

    def test_label_icon(self) -> None:
        # эмодзи вне BMP занимает 2 символа UTF-16
        m = msg("😀 Купить", [{"type": "custom_emoji", "offset": 0, "length": 2, "custom_emoji_id": "77"}])
        self.assertEqual(parse_label(m), ("Купить", "77"))

    def test_button_list(self) -> None:
        text = "😀 Канал - https://t.me/a | Чат @chat\nСайт site.com\nбез ссылки"
        m = msg(text, [{"type": "custom_emoji", "offset": 0, "length": 2, "custom_emoji_id": "9"}])
        rows, errors = parse_button_list(m)
        self.assertEqual(rows, [[("Канал", "https://t.me/a", "9"), ("Чат", "https://t.me/chat", None)],
                                [("Сайт", "https://site.com", None)]])
        self.assertEqual(len(errors), 1)


if __name__ == "__main__":
    unittest.main()
