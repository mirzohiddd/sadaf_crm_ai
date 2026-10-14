"""/api/leads/baza — Leadlar Kanbanidagi "BAZA" ustuni (PostgreSQL).

* ``GET  /api/leads/baza?dateFrom=&dateTo=`` — PostgreSQL'dagi, CRM'da hali
  bo'lmagan, takrorlanmaydigan leadlar (sana filtri — lead kelgan kun).
* ``POST /api/leads/baza/{id}/import`` — yozuvni CRM leadiga aylantiradi
  (Kanbanda BAZA kartasini boshqa ustunga sudrab o'tkazish). Lead CRM'ga
  tushgach BAZA ustunida boshqa ko'rinmaydi.
"""
from __future__ import annotations

import threading
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from .. import config, storage
from ..deps import is_global, require
from ..services import assignment, baza, notify
from . import leads as leads_router

router = APIRouter(prefix="/api/leads/baza", tags=["leads"])

# Bir yozuvni ikki hodim bir vaqtda o'tkazsa ham CRM'da bitta lead bo'ladi.
_import_lock = threading.Lock()


class BazaImportIn(BaseModel):
    stage: str = "Yangi"


def _unavailable(exc: Exception) -> HTTPException:
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))


@router.get("")
def list_baza(
    date_from: str | None = Query(default=None, alias="dateFrom"),
    date_to: str | None = Query(default=None, alias="dateTo"),
    user: dict[str, Any] = Depends(require("leads", "read")),
) -> dict[str, Any]:
    start, end = leads_router.parse_range(date_from, date_to)
    if not baza.enabled():
        return {"enabled": False, "items": [], "count": 0, "truncated": False, "table": config.BAZA_TABLE}
    try:
        result = baza.list_leads(start, end)
    except baza.BazaError as exc:
        raise _unavailable(exc) from exc
    return {"enabled": True, "items": result["items"], "count": len(result["items"]),
            "truncated": result["truncated"], "table": config.BAZA_TABLE}


def _find_existing(row: dict[str, Any]) -> dict[str, Any] | None:
    key = baza.phone_key(row.get("phone"))
    for lead in storage.read("leads"):
        if baza.is_imported_from_current(lead) and str(lead["bazaId"]) == str(row["id"]):
            return lead
        if key and baza.phone_key(lead.get("phone")) == key:
            return lead
    return None


@router.post("/{row_id}/import", status_code=status.HTTP_201_CREATED)
def import_baza_lead(
    row_id: str,
    body: BazaImportIn,
    user: dict[str, Any] = Depends(require("leads", "write")),
) -> dict[str, Any]:
    if not baza.enabled():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "PostgreSQL BAZA ulanmagan (DATABASE_URL).")
    if body.stage not in leads_router.STAGES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Noma'lum bosqich: {body.stage}")
    try:
        row = baza.get_row(row_id)
    except baza.BazaError as exc:
        raise _unavailable(exc) from exc
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "BAZA yozuvi topilmadi.")

    with _import_lock:
        existing = _find_existing(row)
        if existing or row.get("crmLeadId"):
            lead_id = int(existing["id"]) if existing else row.get("crmLeadId")
            raise HTTPException(status.HTTP_409_CONFLICT, f"Bu lead CRM'da allaqachon mavjud (#{lead_id}).")

        # Mas'ul: Operator/Admin — o'zi; Bosh menejer — hodimlar orasida round-robin
        # (create_lead bilan bir xil). Bosh menejerning o'ziga lead tushmaydi.
        if not is_global(user, "leads") and user.get("role") in ("operator", "admin"):
            manager = user.get("name", "")
        else:
            assigned = assignment.next_admin()
            manager = assigned.get("name", "") if assigned else ""

        comment = row.get("comment", "")
        data: dict[str, Any] = {
            "name": row.get("name", ""),
            "phone": row.get("phone", ""),
            "tour": row.get("tour", ""),
            # BAZA'da yo'q ma'lumot bo'sh qoladi (Kanbanda "—"), taxmin qilinmaydi
            "people": None,
            "amount": None,
            "manager": manager,
            "source": row.get("source") or "Baza",
            "stage": body.stage,
            "telegram": row.get("telegram", ""),
            "city": row.get("city", ""),
            "date": row.get("date") or storage.today_uz(),
            "time": row.get("time") or storage.now_time(),
            # Lead BAZA'ga kelgan haqiqiy vaqt saqlanadi (sana filtri uchun).
            "arrivedAt": row.get("arrivedAt") or leads_router.arrival.now_arrival(),
            "arrivedAtSource": "baza",
            "bazaId": str(row["id"]),
            "bazaTable": config.BAZA_TABLE,
            "ownerId": int(user["id"]),
            "seenBy": [int(user["id"])],
            "history": [{
                "title": body.stage,
                "date": f"{storage.today_uz()} {storage.now_time()}",
                "author": user.get("name", ""),
                "text": f"BAZA → {body.stage}",
                "color": "bg-emerald-500",
            }],
        }
        data.update(leads_router._comments_patch(
            [leads_router._new_comment(comment, None)] if comment.strip() else []))
        created = storage.insert("leads", data)

    baza.mark_imported(str(row["id"]), int(created["id"]))
    leads_router._sync_sale(created)
    leads_router._sync_client(created)
    notify.log(user.get("name", ""), "create", "lead", created.get("name", ""),
               {"from": "BAZA", "stage": body.stage}, actor_id=int(user["id"]))
    targets = [i for i in leads_router._managers_of(created.get("manager", "")) if i != int(user["id"])]
    notify.push(targets, "Sizga yangi lead biriktirildi", created.get("name", ""),
                amount=0.0, kind="lead", link="/ledlar")
    return created