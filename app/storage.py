"""JSON fayllar bilan xavfsiz ishlash.

Har bir kolleksiya alohida .json fayl. Yozish atomar (avval .tmp faylga
yoziladi, keyin os.replace bilan almashtiriladi) — shuning uchun jarayon
yozish o'rtasida to'xtab qolsa ham fayl buzilmaydi.

Har bir fayl uchun alohida RLock: bir vaqtda ikki so'rov bitta faylni
yozmaydi.
"""
from __future__ import annotations
import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .config import DATA_DIR
from .services.realtime import notify_collection_changed

# O'zbekiston (Asia/Tashkent) doim UTC+5 — yozgi vaqtga o'tish yo'q. Shuning
# uchun tashqi kutubxona (zoneinfo/tzdata) shart emas: bu offset serverning
# qayerda joylashganidan (masalan Render — odatda UTC) qat'i nazar har doim
# to'g'ri bo'ladi. Eslatmalar (reminders) va barcha ko'rsatiladigan
# sana/vaqtlar shu zonaga nisbatan hisoblanadi.
TASHKENT_TZ = timezone(timedelta(hours=5), name="Asia/Tashkent")

COLLECTIONS = (
    "users",
    "leads",
    "clients",
    "tasks",
    "notifications",
    "sales",
    "tours",
    "settings",
    "attendance",
    "ai_chats",
    "activity",
    "lead_assignment",
    "analytics_goals",
    "reminders",
    # AI Call Center (qo'shimcha, backward-compatible): har bir qo'ng'iroq
    # alohida yozuv; transcript va AI analiz alohida kolleksiyalarda —
    # katta matnlar asosiy calls.json ni og'irlashtirmaydi.
    "calls",
    "call_transcripts",
    "call_analyses",
)

_locks: dict[str, threading.RLock] = {name: threading.RLock() for name in COLLECTIONS}
_global_lock = threading.RLock()


def _path(name: str) -> Path:
    return DATA_DIR / f"{name}.json"


def _lock(name: str) -> threading.RLock:
    with _global_lock:
        if name not in _locks:
            _locks[name] = threading.RLock()
        return _locks[name]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def now_tashkent() -> datetime:
    """Joriy vaqtni doim Asia/Tashkent (UTC+5) zonasida qaytaradi —
    serverning haqiqiy joylashuvidan (Render'da odatda UTC) qat'i nazar."""
    return datetime.now(TASHKENT_TZ)


def today_uz() -> str:
    """Frontend ishlatadigan DD.MM.YYYY formati (Asia/Tashkent bo'yicha)."""
    return now_tashkent().strftime("%d.%m.%Y")


def now_time() -> str:
    """HH:MM, Asia/Tashkent bo'yicha."""
    return now_tashkent().strftime("%H:%M")


def read(name: str) -> list[dict[str, Any]]:
    """Kolleksiyani o'qish. Fayl yo'q yoki buzuq bo'lsa — bo'sh ro'yxat."""
    path = _path(name)
    with _lock(name):
        if not path.exists():
            return []
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            return []
    return data if isinstance(data, list) else []


def write(name: str, rows: list[dict[str, Any]]) -> None:
    """Kolleksiyani atomar yozish."""
    path = _path(name)
    tmp = path.with_suffix(".json.tmp")
    with _lock(name):
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    # Har qanday yozuvdan keyin — barcha ulangan mijozlarga real vaqtda signal.
    # Bitta joyga ulanish tufayli insert/update/delete/mutate — hammasi qamrab olinadi.
    notify_collection_changed(name)


def mutate(name: str, fn: Callable[[list[dict[str, Any]]], Any]) -> Any:
    """O'qish + o'zgartirish + yozishni bitta lock ichida bajaradi.

    fn ro'yxatni joyida o'zgartiradi va istalgan qiymat qaytaradi.
    """
    with _lock(name):
        rows = read(name)
        result = fn(rows)
        write(name, rows)
        return result


def next_id(rows: list[dict[str, Any]]) -> int:
    ids = [int(r.get("id") or 0) for r in rows]
    return (max(ids) + 1) if ids else 1


# ——— Umumiy CRUD yordamchilari ———


def get_all(name: str) -> list[dict[str, Any]]:
    return read(name)


def get_one(name: str, item_id: int) -> dict[str, Any] | None:
    for row in read(name):
        if int(row.get("id", 0)) == int(item_id):
            return row
    return None


def insert(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    def _do(rows: list[dict[str, Any]]) -> dict[str, Any]:
        row = dict(payload)
        row["id"] = next_id(rows)
        row.setdefault("createdAt", now_iso())
        row["updatedAt"] = now_iso()
        rows.insert(0, row)
        return row

    return mutate(name, _do)


def update(name: str, item_id: int, patch: dict[str, Any]) -> dict[str, Any] | None:
    def _do(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        for row in rows:
            if int(row.get("id", 0)) == int(item_id):
                clean = {k: v for k, v in patch.items() if k != "id"}
                row.update(clean)
                row["updatedAt"] = now_iso()
                return row
        return None

    return mutate(name, _do)


def delete(name: str, item_id: int) -> dict[str, Any] | None:
    def _do(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        for i, row in enumerate(rows):
            if int(row.get("id", 0)) == int(item_id):
                return rows.pop(i)
        return None

    return mutate(name, _do)


def ensure_files() -> None:
    """Barcha kolleksiya fayllari mavjudligini kafolatlaydi."""
    for name in COLLECTIONS:
        if not _path(name).exists():
            write(name, [])