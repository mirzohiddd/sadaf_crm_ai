"""Bir xil CRUD naqshiga ega resurslar: mijozlar, turlar, savdolar.

Har biri uchun alohida router yozish o'rniga bitta fabrika ishlatiladi —
ruxsat tekshiruvi, scoping, jurnal va bildirishnoma bir joyda.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, status

from .. import storage
from ..deps import ensure_access, is_global, require, scope_rows
from ..schemas import Loose
from ..services import notify


def make_router(
    *,
    collection: str,
    prefix: str,
    entity: str,
    scoped: bool,
    notify_admins: bool = False,
    notify_title: str = "",
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=[collection])

    @router.get("")
    def list_rows(user: dict[str, Any] = Depends(require(collection, "read"))) -> list[dict[str, Any]]:
        rows = storage.read(collection)
        return scope_rows(rows, user) if scoped else rows

    @router.post("", status_code=status.HTTP_201_CREATED)
    def create_row(
        body: Loose, user: dict[str, Any] = Depends(require(collection, "write"))
    ) -> dict[str, Any]:
        data = {k: v for k, v in body.model_dump().items() if k != "id"}
        if scoped:
            data["ownerId"] = int(user["id"])
            if user.get("role") == "operator" or not data.get("manager"):
                data["manager"] = user.get("name", "")
        data.setdefault("date", storage.today_uz())

        created = storage.insert(collection, data)
        title = str(created.get("name") or created.get("client") or f"#{created['id']}")
        notify.log(user.get("name", ""), "create", entity, title,
                   {"amount": created.get("amount") or created.get("total") or 0},
                   actor_id=int(user["id"]))
        if notify_admins:
            notify.broadcast_admins(
                notify_title or f"Yangi {entity}", title,
                exclude=int(user["id"]), kind=entity,
            )
        return created

    @router.put("/{row_id}")
    def update_row(
        row_id: int, body: Loose, user: dict[str, Any] = Depends(require(collection, "write"))
    ) -> dict[str, Any]:
        current = storage.get_one(collection, row_id)
        if scoped:
            current = ensure_access(current, user)
        elif current is None:
            ensure_access(None, user)

        patch = {k: v for k, v in body.model_dump().items() if k not in ("id", "ownerId")}
        if scoped and user.get("role") == "operator":
            patch.pop("manager", None)

        updated = storage.update(collection, row_id, patch)
        row = updated or current or {}
        notify.log(user.get("name", ""), "update", entity,
                   str(row.get("name") or row.get("client") or f"#{row_id}"),
                   actor_id=int(user["id"]))
        return row

    @router.delete("/{row_id}")
    def delete_row(
        row_id: int, user: dict[str, Any] = Depends(require(collection, "delete"))
    ) -> dict[str, Any]:
        current = storage.get_one(collection, row_id)
        if scoped:
            current = ensure_access(current, user)
        elif current is None:
            ensure_access(None, user)

        storage.delete(collection, row_id)
        notify.log(user.get("name", ""), "delete", entity,
                   str((current or {}).get("name") or f"#{row_id}"), actor_id=int(user["id"]))
        return {"ok": True, "id": row_id}

    return router


clients_router = make_router(
    collection="clients", prefix="/api/clients", entity="client",
    scoped=True, notify_admins=True, notify_title="Yangi mijoz",
)

tours_router = make_router(
    collection="tours", prefix="/api/tours", entity="tour", scoped=False,
)

sales_router = make_router(
    collection="sales", prefix="/api/sales", entity="sale",
    scoped=True, notify_admins=True, notify_title="Yangi savdo",
)
