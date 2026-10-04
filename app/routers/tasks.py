"""/api/tasks — xodimlar bir-biriga topshiriq beradi."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from .. import storage
from ..deps import get_current_user, is_global, visible_names
from ..schemas import Loose, TaskIn
from ..services import notify

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def _ids_named(name: str) -> list[int]:
    return [int(u["id"]) for u in storage.read("users") if u.get("name") == name]


def _visible(rows: list[dict[str, Any]], user: dict[str, Any]) -> list[dict[str, Any]]:
    if is_global(user):
        return rows
    names = visible_names(user) or set()
    return [t for t in rows if t.get("to") in names or t.get("from") in names]


def _can_touch(task: dict[str, Any], user: dict[str, Any]) -> bool:
    if is_global(user):
        return True
    me = user.get("name")
    if task.get("to") == me or task.get("from") == me:
        return True
    # Menejer jamoasidagi vazifalarni ham boshqaradi
    return user.get("role") == "manager" and task.get("to") in (visible_names(user) or set())


@router.get("")
def list_tasks(user: dict[str, Any] = Depends(get_current_user)) -> list[dict[str, Any]]:
    return _visible(storage.read("tasks"), user)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_task(body: TaskIn, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    data = body.model_dump()
    data["from"] = user.get("name", "")
    data["fromId"] = int(user["id"])
    data.setdefault("due", storage.today_uz())

    if not data.get("to"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Vazifa kimga berilishini tanlang.")

    names = visible_names(user)
    if names is not None and data["to"] not in names:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Bu hodimga vazifa berish huquqi yo'q.")

    created = storage.insert("tasks", data)
    notify.log(user.get("name", ""), "create", "task", created.get("title", ""), actor_id=int(user["id"]))
    notify.push(
        [i for i in _ids_named(created.get("to", "")) if i != int(user["id"])],
        "Sizga yangi vazifa berildi",
        f"{created.get('title')} — {created.get('from')} ({created.get('priority')})",
        kind="task",
        link="/dashboard",
    )
    return created


@router.put("/{task_id}")
def update_task(
    task_id: int, body: Loose, user: dict[str, Any] = Depends(get_current_user)
) -> dict[str, Any]:
    current = storage.get_one("tasks", task_id)
    if not current:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Vazifa topilmadi.")
    if not _can_touch(current, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Bu vazifa sizga tegishli emas.")

    patch = {k: v for k, v in body.model_dump().items() if k not in ("id", "from", "fromId")}
    updated = storage.update("tasks", task_id, patch)
    return updated or current


@router.patch("/{task_id}/toggle")
def toggle_task(task_id: int, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    current = storage.get_one("tasks", task_id)
    if not current:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Vazifa topilmadi.")
    if not _can_touch(current, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Bu vazifa sizga tegishli emas.")

    done = not bool(current.get("done"))
    updated = storage.update("tasks", task_id, {"done": done})

    if done:
        notify.log(user.get("name", ""), "done", "task", current.get("title", ""), actor_id=int(user["id"]))
        notify.push(
            [i for i in _ids_named(current.get("from", "")) if i != int(user["id"])],
            "Vazifa bajarildi",
            f"{current.get('title')} — {user.get('name')}",
            kind="task",
        )
    return updated or current


@router.delete("/{task_id}")
def delete_task(task_id: int, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    current = storage.get_one("tasks", task_id)
    if not current:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Vazifa topilmadi.")
    if not _can_touch(current, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Bu vazifa sizga tegishli emas.")

    storage.delete("tasks", task_id)
    return {"ok": True, "id": task_id}
