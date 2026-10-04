"""Lead assignment — yangi leadlarni aktiv adminlar orasida navbat bilan
(round-robin) taqsimlash.

Holat (oxirgi biriktirilgan admin ID) alohida "lead_assignment"
kolleksiyasida (JSON faylda) saqlanadi — shuning uchun server qayta ishga
tushganda ham navbat 1-admin'dan qaytadan boshlanmaydi, oxirgi to'xtagan
joyidan davom etadi.

Faqat roli "admin" va "active" bo'lgan foydalanuvchilar navbatga kiradi.
"""
from __future__ import annotations

from typing import Any

from .. import storage

STATE_ID = 1


def _get_state() -> dict[str, Any]:
    """Navbat holatini o'qiydi, birinchi chaqiriqda bo'sh holat yaratadi."""
    row = storage.get_one("lead_assignment", STATE_ID)
    if row is None:
        row = storage.insert("lead_assignment", {"id": STATE_ID, "lastAdminId": None})
    return row


def active_admins() -> list[dict[str, Any]]:
    """Round-robinga kiruvchi foydalanuvchilar: roli 'admin' va faol,
    ID bo'yicha barqaror (har doim bir xil) tartiblangan."""
    admins = [
        u for u in storage.read("users")
        if u.get("role") == "admin" and u.get("active", True)
    ]
    admins.sort(key=lambda u: int(u.get("id", 0)))
    return admins


def _rotate_index(admins: list[dict[str, Any]], last_admin_id: int | None) -> int:
    """Oxirgi biriktirilgan admin ID'dan keyingi navbatdagi indeksni topadi.

    Agar oxirgi admin endi ro'yxatda bo'lmasa (masalan, faolsizlantirilgan
    yoki o'chirilgan) — navbat ro'yxat boshidan davom etadi.
    """
    ids = [int(a["id"]) for a in admins]
    if last_admin_id is not None and int(last_admin_id) in ids:
        return (ids.index(int(last_admin_id)) + 1) % len(ids)
    return 0


def peek_next_admin() -> dict[str, Any] | None:
    """Holatni o'zgartirmasdan — navbatda kim turganini ko'rsatadi
    (Super Admin uchun holatni ko'rish maqsadida)."""
    admins = active_admins()
    if not admins:
        return None
    last_admin_id = _get_state().get("lastAdminId")
    return admins[_rotate_index(admins, last_admin_id)]


def next_admin() -> dict[str, Any] | None:
    """Navbatdagi faol adminni qaytaradi VA holatni saqlaydi (keyingi
    lead uchun navbat bir qadam siljiydi). Aktiv admin topilmasa — None.
    """
    admins = active_admins()
    if not admins:
        return None
    state = _get_state()
    idx = _rotate_index(admins, state.get("lastAdminId"))
    chosen = admins[idx]
    storage.update("lead_assignment", STATE_ID, {"lastAdminId": int(chosen["id"])})
    return chosen
