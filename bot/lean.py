"""Заметно снижает память aiogram, не меняя его поведения.

aiogram откладывает сборку моделей pydantic, но в конце импорта сам собирает все сотни разом.
Каждая модель получает свой валидатор со вложенными типами, отсюда около 130 МБ.
Здесь эта общая сборка пропускается: разбор обновления идёт через один валидатор Update,
а остальные модели собираются при первом обращении, и таких обычно несколько десятков.

Чтобы отложенная сборка находила типы, на которые модели ссылаются только в аннотациях,
их имена добавляются в модули. Версия aiogram закреплена, а тесты проверяют сборку всех моделей.
Выключается переменной LEAN_IMPORT=0.
"""
from __future__ import annotations

import os
import sys
from typing import Any


def install() -> bool:
    if os.environ.get("LEAN_IMPORT") == "0" or "aiogram.types" in sys.modules:
        return False
    try:
        import pydantic
    except ImportError:
        return False

    namespace: dict[str, Any] = {}
    original = pydantic.BaseModel.__dict__["model_rebuild"]

    def skip_rebuild(cls: type, *, _types_namespace: Any = None, **kwargs: Any) -> None:
        if _types_namespace:
            namespace.update(_types_namespace)

    pydantic.BaseModel.model_rebuild = classmethod(skip_rebuild)  # type: ignore[method-assign,assignment]
    try:
        import aiogram.types  # noqa: F401
    except ImportError:
        return False
    finally:
        pydantic.BaseModel.model_rebuild = original  # type: ignore[method-assign]

    modules = {
        value.__module__
        for value in namespace.values()
        if isinstance(value, type) and issubclass(value, pydantic.BaseModel)
    }
    for name in modules:
        module_globals = vars(sys.modules[name])
        for key, value in namespace.items():
            module_globals.setdefault(key, value)
    return True
