"""Turlar (tour paketlari) uchun ma'lumotni tekshirish va normallashtirish.

Asosiy maydonlar:
  * ``name``          — tur nomi (majburiy)
  * ``country``       — yo'nalish (majburiy)
  * ``departureDate`` — ketish sanasi, ``YYYY-MM-DD`` (majburiy)
  * ``price``         — narx, so'mda (majburiy, musbat)
  * ``currency``      — yangi turlar uchun doim ``"UZS"``

Avval narx USD da saqlangan eski turlarda ``currency`` maydoni yo'q — ular
o'zgarishsiz qoladi (frontend ularni "$" bilan ko'rsatadi). Tahrirlashda
narx yangidan kiritilsa, tur so'mga o'tkaziladi.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import HTTPException, status

CURRENCY = "UZS"
REQUIRED_TEXT = {"name": "Tur nomini kiriting.", "country": "Yo'nalishni tanlang."}


def _bad(msg: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, msg)


def _valid_iso(value: Any) -> bool:
    try:
        date.fromisoformat(str(value))
        return len(str(value)) == 10
    except ValueError:
        return False


def prepare(data: dict[str, Any], *, partial: bool) -> dict[str, Any]:
    """Kelgan ma'lumotni tekshiradi va tozalangan nusxasini qaytaradi.

    ``partial=True`` (tahrirlash) — faqat yuborilgan maydonlar tekshiriladi.
    """
    out = dict(data)

    for key, msg in REQUIRED_TEXT.items():
        if key in out or not partial:
            value = str(out.get(key) or "").strip()
            if not value:
                raise _bad(msg)
            out[key] = value

    if "departureDate" in out or not partial:
        dep = str(out.get("departureDate") or "").strip()
        if not dep:
            raise _bad("Ketish sanasini tanlang.")
        if not _valid_iso(dep):
            raise _bad("Ketish sanasi YYYY-MM-DD formatida bo'lishi kerak.")
        out["departureDate"] = dep

    if "price" in out or not partial:
        try:
            price = float(out.get("price"))
        except (TypeError, ValueError):
            raise _bad("Narxni so'mda kiriting.") from None
        if price <= 0:
            raise _bad("Narx 0 dan katta bo'lishi kerak.")
        # So'm — butun son
        out["price"] = int(round(price))
        out["currency"] = CURRENCY

    end = str(out.get("endDate") or "").strip()
    dep = str(out.get("departureDate") or "").strip()
    if end and dep and _valid_iso(end) and end < dep:
        raise _bad("Qaytish sanasi ketish sanasidan oldin bo'lmasin.")

    return out
