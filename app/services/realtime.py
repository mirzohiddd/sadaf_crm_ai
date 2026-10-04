"""Real vaqtli sinxronizatsiya — WebSocket orqali.

Har qanday kolleksiya (leadlar, mijozlar, vazifalar, hodimlar va h.k.)
JSON faylga yozilganda, shu yerdagi `manager` barcha ulangan mijozlarga
"bu bo'lim o'zgardi" degan qisqa xabar yuboradi. Frontend xabarni olib,
faqat o'sha bo'limni serverdan qayta yuklaydi — sahifa umuman qayta
yuklanmaydi va foydalanuvchi ishini davom ettiraveradi.

Storage (JSON fayllar) — yagona haqiqat manbai. WebSocket faqat "signal":
xabar yo'qolib qolsa ham keyingi mutatsiya baribir signal yuboradi va/yoki
frontenddagi zaxira polling (60s) barini tenglashtiradi.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger("sadaf.realtime")


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[WebSocket, int | None] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Ishga tushishda joriy event loop saqlanadi — sync kontekstdan
        (masalan storage.write) xabar yuborish uchun kerak bo'ladi."""
        self._loop = loop

    async def connect(self, ws: WebSocket, user_id: int | None) -> None:
        await ws.accept()
        self._connections[ws] = user_id

    def disconnect(self, ws: WebSocket) -> None:
        self._connections.pop(ws, None)

    async def _broadcast(self, message: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        for ws in list(self._connections.keys()):
            try:
                await ws.send_json(message)
            except Exception:  # noqa: BLE001 — ulanish uzilgan bo'lishi mumkin
                dead.append(ws)
        for ws in dead:
            self._connections.pop(ws, None)

    def broadcast(self, message: dict[str, Any]) -> None:
        """Har qanday joydan (sync yoki async) xavfsiz chaqiriladi."""
        if not self._connections or self._loop is None:
            return
        try:
            if self._loop.is_running():
                asyncio.run_coroutine_threadsafe(self._broadcast(message), self._loop)
        except RuntimeError:
            logger.debug("Realtime broadcast o'tkazib yuborildi (loop tayyor emas).")


manager = ConnectionManager()

# Bu kolleksiyalar shaxsiy emas — o'zgarsa hammaga signal boradi.
# ai_chats — shaxsiy suhbat tarixi, broadcast qilinmaydi.
_SKIP_BROADCAST = {"ai_chats"}


def notify_collection_changed(collection: str) -> None:
    """storage.write() har chaqirilganda ishga tushadi."""
    if collection in _SKIP_BROADCAST:
        return
    manager.broadcast({"type": "collection", "collection": collection})
