"""Lead eslatmalari (Reminder).

- ``GET  /api/reminders``                  — "Eslatmalar" sahifasi: foydalanuvchiga
  ochiq barcha leadlarning eslatmalari (lead nomi/ID bilan birga)
- ``POST /api/reminders``                  — "Eslatmalar" sahifasidan yangi eslatma
  (body ichida ``leadId``)
- ``GET  /api/leads/{lead_id}/reminders``  — shu leadga tegishli eslatmalar
- ``POST /api/leads/{lead_id}/reminders``  — yangi eslatma (sana, vaqt, izoh)
- ``PATCH /api/reminders/{id}/done``       — bajarildi/bajarilmadi belgisi
- ``DELETE /api/reminders/{id}``           — o'chirish

Ikkala sahifa (lead paneli va umumiy "Eslatmalar" bo'limi) bitta
``reminders`` kolleksiyasidan o'qiydi — shuning uchun lead ichida qo'shilgan
eslatma umumiy ro'yxatda ham avtomatik ko'rinadi va takrorlanmaydi
(har bir eslatma bitta yozuv, bitta ``id``).

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
from ..schemas import ReminderCreateIn, ReminderIn
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


def _can_touch(reminder: dict[str, Any], user: dict[str, Any], lead: dict[str, Any] | None = None) -> bool:
    """Eslatmani bajarildi deb belgilash/o'chirish huquqi bormi.

    - Super Admin (leads uchun global) — har doim.
    - Eslatmani yaratgan kishi — har doim.
    - Lead qaysi menejerga/hodimga tegishli bo'lsa — o'sha kishi ham
      (leads scoping bilan bir xil mantiq: ``deps.owns``).

    ``lead`` oldindan ma'lum bo'lsa (ro'yxat chiqarishda) — qayta o'qilmaydi.
    """
    if is_global(user, "leads"):
        return True
    if int(reminder.get("ownerId", 0) or 0) == int(user["id"]):
        return True
    if lead is None:
        lead = storage.get_one("leads", int(reminder.get("leadId", 0) or 0))
    return bool(lead) and owns(lead, user, "leads")


def _with_meta(reminder: dict[str, Any], user: dict[str, Any], lead: dict[str, Any] | None) -> dict[str, Any]:
    """Eslatmaga ko'rsatish uchun qo'shimcha maydonlar qo'shadi (saqlanmaydi).

    - ``leadName`` — leadning JORIY ismi (lead keyin tahrirlangan bo'lsa ham
      to'g'ri chiqadi); lead topilmasa — eslatmada saqlangan nom qoladi.
    - ``canManage`` — joriy foydalanuvchi bu eslatmani o'chira/belgilay
      oladimi (frontend tugmani shunga qarab ko'rsatadi; backend baribir
      ``_can_touch`` bilan alohida tekshiradi).
    """
    row = dict(reminder)
    if lead:
        row["leadName"] = lead.get("name") or row.get("leadName", "")
        row["leadPhone"] = lead.get("phone", "")
        row["leadStage"] = lead.get("stage", "")
        row["leadManager"] = lead.get("manager", "")
    row["canManage"] = _can_touch(reminder, user, lead)
    return row


def _create(lead_id: int, body: ReminderIn, user: dict[str, Any]) -> dict[str, Any]:
    """Lead ichidan ham, umumiy sahifadan ham ishlatiladigan yagona yaratish mantiqi."""
    lead = ensure_access(storage.get_one("leads", lead_id), user, resource="leads")

    due_at = _due_at(body.date, body.time)
    if not due_at:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Sana yoki vaqt noto'g'ri formatda.")

    data = body.model_dump()
    data.pop("leadId", None)  # leadId alohida, int sifatida yoziladi
    data["note"] = str(data.get("note") or "").strip()
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

    def _insert_unique(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], bool]:
        """Bir xil eslatma (shu lead, shu vaqt, shu matn, hali bajarilmagan)
        allaqachon bo'lsa — yangisi yaratilmaydi, mavjudi qaytariladi.
        Tugma ikki marta bosilsa yoki so'rov qayta yuborilsa dublikat paydo
        bo'lmaydi. Tekshiruv va yozuv bitta lock ichida (atomik)."""
        for row in rows:
            if (
                int(row.get("leadId", 0) or 0) == lead_id
                and row.get("dueAt") == due_at
                and str(row.get("note") or "").strip() == data["note"]
                and not row.get("done")
            ):
                return row, False
        row = dict(data)
        row["id"] = storage.next_id(rows)
        row["createdAt"] = storage.now_iso()
        row["updatedAt"] = row["createdAt"]
        rows.insert(0, row)
        return row, True

    created, is_new = storage.mutate("reminders", _insert_unique)
    if is_new:
        notify.log(user.get("name", ""), "create", "reminder", lead.get("name", ""), actor_id=int(user["id"]))
    return _with_meta(created, user, lead)


@router.get("/api/reminders")
def list_all_reminders(user: dict[str, Any] = Depends(require("leads", "read"))) -> list[dict[str, Any]]:
    """"Eslatmalar" sahifasi: foydalanuvchi ko'ra oladigan barcha leadlarning eslatmalari.

    Ko'rish doirasi lead ro'yxati bilan bir xil (``deps.owns``): Super Admin —
    hammasini, boshqalar — faqat o'ziga/jamoasiga tegishli leadlarnikini.
    Leadi o'chirib yuborilgan (yetim) eslatmalar ko'rsatilmaydi.
    """
    leads = {int(lead.get("id", 0)): lead for lead in storage.read("leads")}
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for reminder in storage.read("reminders"):
        rid = int(reminder.get("id", 0) or 0)
        if rid in seen:  # himoya: bir xil id ikki marta chiqmasin
            continue
        lead = leads.get(int(reminder.get("leadId", 0) or 0))
        if not lead or not owns(lead, user, "leads"):
            continue
        seen.add(rid)
        out.append(_with_meta(reminder, user, lead))
    out.sort(key=lambda r: r.get("dueAt") or "")
    return out


@router.post("/api/reminders", status_code=status.HTTP_201_CREATED)
def create_reminder_for_lead(
    body: ReminderCreateIn, user: dict[str, Any] = Depends(require("leads", "write"))
) -> dict[str, Any]:
    return _create(int(body.leadId), body, user)


@router.get("/api/leads/{lead_id}/reminders")
def list_lead_reminders(
    lead_id: int, user: dict[str, Any] = Depends(require("leads", "read"))
) -> list[dict[str, Any]]:
    lead = ensure_access(storage.get_one("leads", lead_id), user, resource="leads")
    rows = [_with_meta(r, user, lead) for r in storage.read("reminders") if int(r.get("leadId", 0)) == lead_id]
    rows.sort(key=lambda r: r.get("dueAt") or "")
    return rows


@router.post("/api/leads/{lead_id}/reminders", status_code=status.HTTP_201_CREATED)
def create_reminder(
    lead_id: int, body: ReminderIn, user: dict[str, Any] = Depends(require("leads", "write"))
) -> dict[str, Any]:
    return _create(lead_id, body, user)


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