"""Lead eslatmalari (Reminder).

- ``GET  /api/leads/{lead_id}/reminders``  — shu leadga tegishli eslatmalar
- ``POST /api/leads/{lead_id}/reminders``  — yangi eslatma (sana, vaqt, izoh)
- ``PATCH /api/reminders/{id}/done``       — bajarildi/bajarilmadi belgisi
- ``DELETE /api/reminders/{id}``           — o'chirish

Belgilangan vaqt kelganda ``app/services/reminders.py`` dagi fon vazifasi
(background loop) eslatmani "bajarilgan" deb emas, balki "xabar berildi"
(``notified``) deb belgilaydi va tegishli foydalanuvchilarga
``/api/notifications`` orqali bildirishnoma yuboradi — bu esa WebSocket
orqali barcha ulangan mijozlarga zudlik bilan yetib boradi (qarang:
``app/services/realtime.py`` — ``storage.write`` har doim signal beradi).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from .. import storage
from ..deps import ensure_access, get_current_user, is_global, owns, require
from ..schemas import ReminderIn
from ..services import notify

router = APIRouter(tags=["reminders"])


def _due_at(date: str, time: str) -> str | None:
    """``date`` + ``time`` ni solishtirish uchun yagona ISO qatorga aylantiradi.

    Noto'g'ri format kelsa ``None`` qaytaradi — chaqiruvchi 400 xato beradi.
    """
    try:
        return datetime.fromisoformat(f"{date}T{time or '00:00'}").isoformat(timespec="minutes")
    except ValueError:
        return None


def _can_touch(reminder: dict[str, Any], user: dict[str, Any]) -> bool:
    """Eslatmani bajarildi deb belgilash/o'chirish huquqi bormi.

    - Super Admin (leads uchun global) — har doim.
    - Eslatmani yaratgan kishi — har doim.
    - Lead qaysi menejerga/hodimga tegishli bo'lsa — o'sha kishi ham
      (leads scoping bilan bir xil mantiq: ``deps.owns``).
    """
    if is_global(user, "leads"):
        return True
    if int(reminder.get("ownerId", 0)) == int(user["id"]):
        return True
    lead = storage.get_one("leads", int(reminder.get("leadId", 0)))
    return bool(lead) and owns(lead, user, "leads")


@router.get("/api/leads/{lead_id}/reminders")
def list_lead_reminders(
    lead_id: int, user: dict[str, Any] = Depends(require("leads", "read"))
) -> list[dict[str, Any]]:
    ensure_access(storage.get_one("leads", lead_id), user, resource="leads")
    rows = [r for r in storage.read("reminders") if int(r.get("leadId", 0)) == lead_id]
    rows.sort(key=lambda r: r.get("dueAt") or "")
    return rows


@router.post("/api/leads/{lead_id}/reminders", status_code=status.HTTP_201_CREATED)
def create_reminder(
    lead_id: int, body: ReminderIn, user: dict[str, Any] = Depends(require("leads", "write"))
) -> dict[str, Any]:
    lead = ensure_access(storage.get_one("leads", lead_id), user, resource="leads")

    due_at = _due_at(body.date, body.time)
    if not due_at:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Sana yoki vaqt noto'g'ri formatda.")

    data = body.model_dump()
    data.update(
        {
            "leadId": lead_id,
            "leadName": lead.get("name", ""),
            "dueAt": due_at,
            "done": False,
            "notified": False,
            "ownerId": int(user["id"]),
            "createdBy": user.get("name", ""),
        }
    )
    created = storage.insert("reminders", data)
    notify.log(user.get("name", ""), "create", "reminder", lead.get("name", ""), actor_id=int(user["id"]))
    return created


@router.patch("/api/reminders/{reminder_id}/done")
def toggle_reminder(reminder_id: int, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    current = storage.get_one("reminders", reminder_id)
    if not current:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Eslatma topilmadi.")
    if not _can_touch(current, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Bu eslatma sizga tegishli emas.")

    done = not bool(current.get("done"))
    updated = storage.update("reminders", reminder_id, {"done": done})
    if done:
        notify.log(user.get("name", ""), "done", "reminder", current.get("leadName", ""), actor_id=int(user["id"]))
    return updated or current


@router.delete("/api/reminders/{reminder_id}")
def delete_reminder(reminder_id: int, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    current = storage.get_one("reminders", reminder_id)
    if not current:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Eslatma topilmadi.")
    if not _can_touch(current, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Bu eslatma sizga tegishli emas.")

    storage.delete("reminders", reminder_id)
    notify.log(user.get("name", ""), "delete", "reminder", current.get("leadName", ""), actor_id=int(user["id"]))
    return {"ok": True, "id": reminder_id}