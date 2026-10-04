"""/api/attendance — Check in / Check out va ish vaqti hisobi."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from .. import storage
from ..deps import get_current_user, is_global, visible_ids

router = APIRouter(prefix="/api/attendance", tags=["attendance"])


def _hours(check_in: str, check_out: str) -> float:
    try:
        start = datetime.strptime(check_in, "%H:%M")
        end = datetime.strptime(check_out, "%H:%M")
        minutes = (end - start).total_seconds() / 60
        if minutes < 0:  # yarim tundan oshgan smena
            minutes += 24 * 60
        return round(minutes / 60, 2)
    except (ValueError, TypeError):
        return 0.0


def _today_row(user_id: int) -> dict[str, Any] | None:
    today = storage.today_uz()
    return next(
        (
            r
            for r in storage.read("attendance")
            if int(r.get("userId", 0)) == int(user_id) and r.get("date") == today
        ),
        None,
    )


@router.get("/today")
def today(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    row = _today_row(int(user["id"]))
    return {
        "date": storage.today_uz(),
        "checkedIn": bool(row and row.get("checkIn") and not row.get("checkOut")),
        "record": row,
    }


@router.get("")
def history(user: dict[str, Any] = Depends(get_current_user)) -> list[dict[str, Any]]:
    """O'z tarixi; admin/menejer jamoasinikini ham ko'radi."""
    rows = storage.read("attendance")
    if is_global(user):
        return rows
    ids = visible_ids(user) or {int(user["id"])}
    return [r for r in rows if int(r.get("userId", 0)) in ids]


@router.get("/summary")
def summary(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """Dashboard uchun: bu oy ishlangan soat va kunlar."""
    uid = int(user["id"])
    month = datetime.now().strftime(".%m.%Y")
    mine = [
        r
        for r in storage.read("attendance")
        if int(r.get("userId", 0)) == uid and str(r.get("date", "")).endswith(month)
    ]
    total = round(sum(float(r.get("hours") or 0) for r in mine), 2)
    return {
        "days": len(mine),
        "hours": total,
        "avgHours": round(total / len(mine), 2) if mine else 0,
        "today": _today_row(uid),
    }


@router.post("/check-in")
def check_in(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    uid = int(user["id"])
    row = _today_row(uid)
    if row and row.get("checkIn") and not row.get("checkOut"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Siz allaqachon ishni boshlagansiz.")

    if row and row.get("checkOut"):
        # Kun ichida qayta kirish — yangi yozuv ochamiz
        row = None

    return storage.insert(
        "attendance",
        {
            "userId": uid,
            "userName": user.get("name", ""),
            "date": storage.today_uz(),
            "checkIn": storage.now_time(),
            "checkOut": "",
            "hours": 0,
        },
    )


@router.post("/check-out")
def check_out(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    uid = int(user["id"])
    row = _today_row(uid)
    if not row or not row.get("checkIn"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Avval Check in qiling.")
    if row.get("checkOut"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Ish kuni allaqachon yakunlangan.")

    out = storage.now_time()
    updated = storage.update(
        "attendance", int(row["id"]), {"checkOut": out, "hours": _hours(row["checkIn"], out)}
    )
    return updated or row
