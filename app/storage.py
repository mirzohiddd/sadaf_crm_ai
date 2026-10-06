"""JSON fayllar bilan xavfsiz ishlash.

Har bir kolleksiya alohida .json fayl. Yozish atomar (avval .tmp faylga
yoziladi, keyin os.replace bilan almashtiriladi) — shuning uchun jarayon
yozish o'rtasida to'xtab qolsa ham fayl buzilmaydi.

Har bir fayl uchun alohida RLock: bir vaqtda ikki so'rov bitta faylni
yozmaydi.
"""
from __future__ import annotations
import json
import logging
import os
import threading
import uuid
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

logger = logging.getLogger("sadaf.storage")


class StorageCorruptError(RuntimeError):
    """Kolleksiya fayli mavjud, lekin o'qib bo'lmaydi — ustidan yozish xavfli."""


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


def _read_strict(name: str) -> list[dict[str, Any]]:
    """Fayl yo'q bo'lsa — []. Fayl bor, lekin buzuq bo'lsa — StorageCorruptError."""
    path = _path(name)
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        raise StorageCorruptError(f"{path.name} o'qib bo'lmadi: {type(exc).__name__}") from exc
    if not isinstance(data, list):
        raise StorageCorruptError(f"{path.name} ro'yxat emas")
    return data


def read(name: str) -> list[dict[str, Any]]:
    """Kolleksiyani o'qish. Fayl yo'q yoki buzuq bo'lsa — bo'sh ro'yxat (API yiqilmasin)."""
    with _lock(name):
        try:
            return _read_strict(name)
        except StorageCorruptError as exc:
            logger.error("Ma'lumot fayli buzuq: %s", exc)
            return []


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
        # Qat'iy o'qish: fayl buzuq bo'lsa [] deb qabul qilib, ustidan yozib
        # yubormaymiz — aks holda bitta xato o'qish butun kolleksiyani
        # (masalan leads.json) o'chirib yuborardi. Buzuq fayl nusxasi saqlanadi.
        try:
            rows = _read_strict(name)
        except StorageCorruptError:
            path = _path(name)
            backup = path.with_name(f"{path.name}.corrupt-{datetime.now(timezone.utc):%Y%m%d%H%M%S}")
            try:
                backup.write_bytes(path.read_bytes())
            except OSError:
                pass
            logger.error("%s buzuq — yozish to'xtatildi, nusxa: %s", path.name, backup.name)
            raise
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


def storage_id() -> str:
    """Ma'lumotlar papkasining doimiy identifikatori (DATA_DIR/.storage_id).

    Papka yangidan yaratilsa (masalan Render diski tozalansa) — yangi ID
    paydo bo'ladi. Google Sheets skripti shu orqali backend ma'lumotlari
    yo'qolganini sezadi va leadlarni qayta yuboradi.
    """
    path = DATA_DIR / ".storage_id"
    with _global_lock:
        try:
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return value
        except OSError:
            pass
        value = uuid.uuid4().hex
        path.write_text(value, encoding="utf-8")
        return value


def ensure_files() -> None:
    """Barcha kolleksiya fayllari mavjudligini kafolatlaydi."""
    for name in COLLECTIONS:
        if not _path(name).exists():
            write(name, [])