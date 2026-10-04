"""/api/notifications — har bir account uchun alohida."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from .. import storage
from ..deps import get_current_user

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


def _mine(user: dict[str, Any]) -> list[dict[str, Any]]:
    uid = int(user["id"])
    return [n for n in storage.read("notifications") if int(n.get("userId", 0)) == uid]


@router.get("")
def list_notifications(user: dict[str, Any] = Depends(get_current_user)) -> list[dict[str, Any]]:
    return _mine(user)


@router.get("/unread-count")
def unread_count(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, int]:
    return {"count": len([n for n in _mine(user) if not n.get("read")])}


@router.patch("/{notification_id}/read")
def mark_read(notification_id: int, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    row = storage.get_one("notifications", notification_id)
    if not row or int(row.get("userId", 0)) != int(user["id"]):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bildirishnoma topilmadi.")
    return storage.update("notifications", notification_id, {"read": True}) or row


@router.patch("/read-all")
def mark_all_read(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    uid = int(user["id"])

    def _do(rows: list[dict[str, Any]]) -> None:
        for row in rows:
            if int(row.get("userId", 0)) == uid:
                row["read"] = True

    storage.mutate("notifications", _do)
    return {"ok": True}


@router.delete("/{notification_id}")
def remove(notification_id: int, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    row = storage.get_one("notifications", notification_id)
    if not row or int(row.get("userId", 0)) != int(user["id"]):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bildirishnoma topilmadi.")
    storage.delete("notifications", notification_id)
    return {"ok": True, "id": notification_id}
