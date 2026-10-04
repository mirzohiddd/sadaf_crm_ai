"""Bildirishnomalar va faoliyat jurnali."""
from __future__ import annotations

from typing import Any, Iterable

from .. import storage

MAX_ACTIVITY = 1000
MAX_NOTIFICATIONS = 800

ENTITY_LABEL = {
    "lead": "Lead",
    "client": "Mijoz",
    "tour": "Tur",
    "employee": "Hodim",
    "task": "Vazifa",
    "sale": "Savdo",
    "reminder": "Eslatma",
    "call": "Qo'ng'iroq",
}


def admin_ids() -> list[int]:
    return [
        int(u["id"])
        for u in storage.read("users")
        if u.get("role") in ("super_admin", "admin") and u.get("active", True)
    ]


def push(
    user_ids: Iterable[int],
    title: str,
    detail: str = "",
    amount: float = 0,
    direction: str = "up",
    kind: str = "info",
    link: str = "",
) -> None:
    """Ko'rsatilgan foydalanuvchilarga bildirishnoma qo'shadi."""
    targets = sorted({int(u) for u in user_ids if u is not None})
    if not targets:
        return

    def _do(rows: list[dict[str, Any]]) -> None:
        base = storage.next_id(rows)
        for offset, uid in enumerate(targets):
            rows.insert(
                0,
                {
                    "id": base + offset,
                    "userId": uid,
                    "title": title,
                    "detail": detail,
                    "amount": amount,
                    "dir": direction,
                    "kind": kind,
                    "link": link,
                    "read": False,
                    "date": storage.today_uz(),
                    "time": storage.now_time(),
                    "ts": storage.now_iso(),
                },
            )
        del rows[MAX_NOTIFICATIONS:]

    storage.mutate("notifications", _do)


def broadcast_admins(title: str, detail: str = "", exclude: int | None = None, **kw) -> None:
    ids = [i for i in admin_ids() if exclude is None or i != int(exclude)]
    push(ids, title, detail, **kw)


def log(
    actor: str,
    action: str,
    entity: str,
    title: str,
    meta: dict[str, Any] | None = None,
    actor_id: int | None = None,
) -> None:
    """Faoliyat jurnaliga yozadi (create | update | delete | stage | done)."""

    def _do(rows: list[dict[str, Any]]) -> None:
        rows.insert(
            0,
            {
                "id": storage.next_id(rows),
                "actor": actor,
                "actorId": actor_id,
                "action": action,
                "entity": entity,
                "title": title,
                "meta": meta or {},
                "ts": storage.now_iso(),
            },
        )
        del rows[MAX_ACTIVITY:]

    storage.mutate("activity", _do)


def activity_text(row: dict[str, Any]) -> str:
    what = ENTITY_LABEL.get(row.get("entity", ""), row.get("entity", ""))
    action = row.get("action")
    meta = row.get("meta") or {}
    if action == "stage":
        return f"{what} bosqichi: {meta.get('from')} → {meta.get('to')}"
    if action == "create":
        return f"{what} qo'shildi"
    if action == "update":
        return f"{what} tahrirlandi"
    if action == "delete":
        return f"{what} o'chirildi"
    if action == "done":
        return "Vazifa bajarildi"
    if action == "comment":
        return f"{what}ga kommentariya qo'shildi"
    if action == "analyze":
        return f"{what} AI tomonidan tahlil qilindi"
    return what