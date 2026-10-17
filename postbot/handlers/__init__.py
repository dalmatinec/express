from aiogram import Router

from . import admin, buttons, chats, common, editor, fallback, history, publish


def build_router() -> Router:
    """Порядок важен: сначала меню и события каналов, в конце то, что никуда не подошло."""
    root = Router(name="root")
    for module in (chats, common, editor, buttons, publish, history, admin, fallback):
        root.include_router(module.router)
    return root
