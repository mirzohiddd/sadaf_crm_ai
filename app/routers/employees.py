"""/api/employees — hodim hisoblari (faqat Super Admin / Admin yaratadi)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from .. import storage
from ..deps import get_current_user, public_user, require, team_of
from ..schemas import EmployeeIn, EmployeeUpdate
from ..security import ROLE_LABELS, ROLES, hash_password
from ..seed import ensure_settings
from ..services import notify

router = APIRouter(prefix="/api/employees", tags=["employees"])


def _login_taken(login: str, exclude_id: int | None = None) -> bool:
    login = login.strip().lower()
    return any(
        str(u.get("login", "")).lower() == login and int(u["id"]) != int(exclude_id or 0)
        for u in storage.read("users")
    )


def _to_frontend(user: dict[str, Any]) -> dict[str, Any]:
    """Backend user -> frontend kutayotgan hodim shakli.

    Parol hech qachon qaytarilmaydi. Frontend jadvalida parol o'rniga
    'CRM parol' ustunida yulduzcha ko'rsatiladi.
    """
    out = {k: v for k, v in user.items() if k != "passwordHash"}
    out["crmLogin"] = user.get("login", "")
    out["crmRole"] = user.get("role", "operator")
    out["crmRoleLabel"] = ROLE_LABELS.get(user.get("role", ""), user.get("role", ""))
    out["crmPassword"] = ""  # parol hech qachon oshkor qilinmaydi
    return out


@router.get("")
def list_employees(user: dict[str, Any] = Depends(require("employees", "read"))) -> list[dict[str, Any]]:
    return [_to_frontend(u) for u in team_of(user)]


@router.get("/roles")
def list_roles(user: dict[str, Any] = Depends(get_current_user)) -> list[dict[str, str]]:
    """Yangi hodim qo'shishda CRM roli tanlovi — faqat 'Admin'.

    Super Admin hisobi faqat birinchi ishga tushirishda (seed) yaratiladi,
    shu sabab bu yerda ko'rsatilmaydi.
    """
    return [{"value": "admin", "label": ROLE_LABELS["admin"]}]


@router.get("/directory")
def directory(user: dict[str, Any] = Depends(get_current_user)) -> list[dict[str, Any]]:
    """Vazifa biriktirish uchun yengil ro'yxat.

    Barcha rollarga ochiq, lekin FAQAT ism/lavozim qaytadi — login, parol
    yoki boshqa maxfiy maydonlar bu yerda yo'q. Shu tufayli operator ham
    hamkasbiga vazifa bera oladi, ammo hisob ma'lumotlarini ko'rmaydi.
    """
    return [
        {
            "id": u["id"],
            "name": u.get("name", ""),
            "position": u.get("position", ""),
            "roleLabel": ROLE_LABELS.get(u.get("role", ""), ""),
        }
        for u in storage.read("users")
        if u.get("active", True)
    ]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_employee(
    body: EmployeeIn, user: dict[str, Any] = Depends(require("employees", "write"))
) -> dict[str, Any]:
    data = body.model_dump()
    login = str(data.pop("crmLogin", "")).strip()
    password = data.pop("crmPassword", None)
    role = data.pop("crmRole", "operator")

    if len(login) < 3:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Login kamida 3 ta belgi bo'lsin.")
    if _login_taken(login):
        raise HTTPException(status.HTTP_409_CONFLICT, "Bu login band. Boshqasini tanlang.")
    if not password or len(password) < 6:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Parol kamida 6 ta belgidan iborat bo'lsin.")
    if role not in ROLES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Noma'lum rol: {role}")
    if role == "super_admin" and user.get("role") != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Super Admin yaratish huquqi yo'q.")

    data.update(
        {
            "login": login,
            "passwordHash": hash_password(password),
            "role": role,
            "active": True,
            "email": data.get("email") or f"{login}@sadaf.uz",
        }
    )
    created = storage.insert("users", data)
    ensure_settings(int(created["id"]))

    notify.log(user.get("name", ""), "create", "employee", created.get("name", ""), actor_id=int(user["id"]))
    notify.broadcast_admins(
        "Yangi hodim qo'shildi",
        f"{created.get('name')} — {ROLE_LABELS.get(role, role)}",
        exclude=int(user["id"]),
        kind="employee",
    )
    notify.push(
        [int(created["id"])],
        "Xush kelibsiz!",
        f"Sizning hisobingiz yaratildi. Login: {login}",
        kind="welcome",
    )
    return _to_frontend(created)


@router.put("/{employee_id}")
def update_employee(
    employee_id: int,
    body: EmployeeUpdate,
    user: dict[str, Any] = Depends(require("employees", "write")),
) -> dict[str, Any]:
    target = storage.get_one("users", employee_id)
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Hodim topilmadi.")
    if target.get("role") == "super_admin" and user.get("role") != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Super Admin hisobini o'zgartira olmaysiz.")

    data = body.model_dump(exclude_none=True)
    patch = {k: v for k, v in data.items() if k not in ("crmLogin", "crmPassword", "crmRole", "id")}

    login = str(data.get("crmLogin", "")).strip()
    if login:
        if _login_taken(login, employee_id):
            raise HTTPException(status.HTTP_409_CONFLICT, "Bu login band.")
        patch["login"] = login

    role = data.get("crmRole")
    if role:
        if role not in ROLES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Noma'lum rol: {role}")
        if role == "super_admin" and user.get("role") != "super_admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Super Admin roli berish huquqi yo'q.")
        patch["role"] = role

    password = data.get("crmPassword")
    if password:
        if len(password) < 6:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Parol kamida 6 ta belgidan iborat bo'lsin.")
        patch["passwordHash"] = hash_password(password)

    updated = storage.update("users", employee_id, patch)
    notify.log(user.get("name", ""), "update", "employee", (updated or {}).get("name", ""), actor_id=int(user["id"]))
    return _to_frontend(updated or target)


@router.delete("/{employee_id}")
def delete_employee(
    employee_id: int, user: dict[str, Any] = Depends(require("employees", "delete"))
) -> dict[str, Any]:
    target = storage.get_one("users", employee_id)
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Hodim topilmadi.")
    if int(employee_id) == int(user["id"]):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "O'z hisobingizni o'chira olmaysiz.")
    if target.get("role") == "super_admin":
        remaining = [
            u for u in storage.read("users")
            if u.get("role") == "super_admin" and int(u["id"]) != int(employee_id)
        ]
        if not remaining:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Oxirgi Super Adminni o'chirib bo'lmaydi.")

    storage.delete("users", employee_id)

    # Hodimga bog'liq sozlamalar va bildirishnomalarni tozalaymiz
    for collection in ("settings", "notifications", "attendance", "ai_chats"):
        storage.write(
            collection,
            [r for r in storage.read(collection) if int(r.get("userId", 0)) != int(employee_id)],
        )

    notify.log(user.get("name", ""), "delete", "employee", target.get("name", ""), actor_id=int(user["id"]))
    return {"ok": True, "id": employee_id}
