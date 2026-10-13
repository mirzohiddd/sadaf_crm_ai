"""Leadning CRM'ga HAQIQIY kelgan vaqti (``arrivedAt``).

Har bir leadda quyidagi texnik maydonlar saqlanadi (ekranda alohida
ko'rinmaydi, sana filtri shularga tayanadi):

* ``arrivedAt``       — lead kelgan vaqt, doim Asia/Tashkent (+05:00) ISO
                         ko'rinishida, masalan ``2026-10-10T14:05:00+05:00``.
                         Shu format tufayli ``arrivedAt[:10]`` — Toshkent
                         bo'yicha kalendar kuni (YYYY-MM-DD).
* ``arrivedAtSource`` — vaqt qayerdan olingani:
    - ``target``  — Meta (target) Lead Ads ``created_time`` ustuni (eng aniq);
    - ``webhook`` — target/Sheets qatorida ``created_time`` bo'lmagan —
                    CRM so'rovni qabul qilgan vaqt;
    - ``website`` — sayt arizasi CRM'ga tushgan vaqt;
    - ``crm``     — CRM ichida qo'lda yaratilgan vaqt;
    - ``baza``    — PostgreSQL BAZA dan olingan yozuvning ``created_at`` vaqti;
    - ``legacy``  — bu funksiyadan oldin yaratilgan lead: ``date``+``time``
                    (bo'lmasa ``createdAt``) dan tiklangan.
* ``targetCreatedAt`` — target'dan kelgan asl ``created_time`` (o'z vaqt
                         zonasi bilan, ISO) — audit uchun o'zgarmasdan saqlanadi.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time
from typing import Any

from ..storage import TASHKENT_TZ, now_tashkent

ARRIVAL_FIELDS = ("arrivedAt", "arrivedAtSource", "targetCreatedAt")

# Vaqt zonasi ko'rsatilmagan matnlar uchun sinab ko'riladigan formatlar.
_NAIVE_FORMATS = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
    "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y",
    "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y",
)


def to_iso(dt: datetime) -> str:
    """Har qanday datetime → Toshkent vaqti bo'yicha ISO (soniyagacha)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TASHKENT_TZ)
    return dt.astimezone(TASHKENT_TZ).isoformat(timespec="seconds")


def now_arrival() -> str:
    return to_iso(now_tashkent())


def parse_moment(value: Any) -> tuple[datetime, bool] | None:
    """Matn/datetime → (aware datetime, vaqt qismi bormi).

    Vaqt zonasi ko'rsatilmagan qiymat Asia/Tashkent deb olinadi. O'qib
    bo'lmasa — None (hech narsa taxmin qilinmaydi).
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=TASHKENT_TZ)), True
    if isinstance(value, date):
        return datetime.combine(value, time(0, 0), TASHKENT_TZ), False
    text = str(value).strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        has_time = bool(re.search(r"\d[T ]\d{1,2}:\d{2}", text))
        return (dt if dt.tzinfo else dt.replace(tzinfo=TASHKENT_TZ)), has_time
    except ValueError:
        pass
    for fmt in _NAIVE_FORMATS:
        try:
            dt = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return dt.replace(tzinfo=TASHKENT_TZ), "%H" in fmt
    return None


def display_parts(dt: datetime, has_time: bool = True) -> tuple[str, str]:
    """CRM ekran formati: (DD.MM.YYYY, HH:MM) — Toshkent vaqti bo'yicha."""
    local = dt.astimezone(TASHKENT_TZ)
    return local.strftime("%d.%m.%Y"), (local.strftime("%H:%M") if has_time else "")


def legacy_arrival(lead: dict[str, Any]) -> str:
    """Eski lead uchun kelish vaqtini tiklaydi: date+time → createdAt → ""."""
    d, t = str(lead.get("date") or "").strip(), str(lead.get("time") or "").strip()
    if d:
        parsed = parse_moment(f"{d} {t}".strip()) or parse_moment(d)
        if parsed:
            return to_iso(parsed[0])
    parsed = parse_moment(lead.get("createdAt"))
    return to_iso(parsed[0]) if parsed else ""


def arrival_of(lead: dict[str, Any]) -> str:
    """Leadning kelish vaqti (saqlangan bo'lsa — o'sha, aks holda tiklangan)."""
    return str(lead.get("arrivedAt") or "") or legacy_arrival(lead)


def arrival_day(lead: dict[str, Any]) -> str:
    """Toshkent bo'yicha kelgan kun, YYYY-MM-DD ("" — noma'lum)."""
    iso = arrival_of(lead)
    if not iso:
        return ""
    parsed = parse_moment(iso)
    return parsed[0].astimezone(TASHKENT_TZ).strftime("%Y-%m-%d") if parsed else ""


def parse_day(value: str | None, field: str) -> date | None:
    """Filtr parametri: YYYY-MM-DD yoki DD.MM.YYYY. Noto'g'ri bo'lsa ValueError."""
    text = (value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"{field}: sana noto'g'ri formatda ({text}). Kutilgan: YYYY-MM-DD yoki DD.MM.YYYY")


def in_range(lead: dict[str, Any], start: date | None, end: date | None) -> bool:
    """Lead [start, end] oralig'ida (ikkala chegara ham kiradi) kelganmi."""
    if start is None and end is None:
        return True
    day = arrival_day(lead)
    if not day:
        return False
    d = date.fromisoformat(day)
    return (start is None or d >= start) and (end is None or d <= end)
