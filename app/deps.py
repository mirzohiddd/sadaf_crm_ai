"""So'rov darajasidagi bog'liqliklar: joriy foydalanuvchi, ruxsat va scoping."""
from __future__ import annotations

from typing import Any, Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import storage
from .security import GLOBAL_ROLES, can, decode_token, pages_for, permissions_for

bearer = HTTPBearer(auto_error=False)


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    """Foydalanuvchini tashqariga chiqarishdan oldin parolni olib tashlaydi."""
    out = {k: v for k, v in user.items() if k != "passwordHash"}
    out["permissions"] = permissions_for(user.get("role", "operator"))
    out["pages"] = pages_for(user.get("role", "operator"))
    return out


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> dict[str, Any]:
    if creds is None or not creds.credentials:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token yuborilmadi.")

    payload = decode_token(creds.credentials)
    if not payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token yaroqsiz yoki muddati tugagan.")

    user = storage.get_one("users", int(payload["sub"]))
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Foydalanuvchi topilmadi.")
    if not user.get("active", True):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Hisob bloklangan.")
    return user


def require(resource: str, action: str = "read") -> Callable[..., dict[str, Any]]:
    """Ruxsatni tekshiruvchi dependency yaratadi."""

    def guard(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
        if not can(user.get("role", "operator"), resource, action):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Sizda '{resource}' uchun '{action}' huquqi yo'q.",
            )
        return user

    return guard


# ——— Ma'lumotlarni ko'rish doirasi ———

# Ba'zi resurslar uchun "global ko'rish" ro'yxati umumiy GLOBAL_ROLES dan farq
# qiladi. Masalan "leads" (lead assignment) da faqat Super Admin barcha
# ma'lumotni ko'radi — oddiy Admin esa faqat o'ziga tegishli leadlarni.
# Boshqa resurslar (clients, sales, tasks, ...) bu yerga kiritilmagani uchun
# ular uchun ilgarigidek GLOBAL_ROLES ishlatiladi — mavjud xatti-harakat
# buzilmaydi.
RESOURCE_GLOBAL_ROLES: dict[str, tuple[str, ...]] = {
    "leads": ("super_admin",),
    # AI Call Center: barcha qo'ng'iroq/recording/transcript/analizlarni
    # faqat Bosh menejer (super_admin) ko'radi. Oddiy menejer (admin) —
    # faqat o'z leadlari va o'z qo'ng'iroqlarini.
    "calls": ("super_admin",),
}


def is_global(user: dict[str, Any], resource: str | None = None) -> bool:
    if resource is not None and resource in RESOURCE_GLOBAL_ROLES:
        return user.get("role") in RESOURCE_GLOBAL_ROLES[resource]
    return user.get("role") in GLOBAL_ROLES


def team_of(user: dict[str, Any], resource: str | None = None) -> list[dict[str, Any]]:
    """Menejer jamoasi: o'zi + unga biriktirilgan hodimlar."""
    users = storage.read("users")
    if is_global(user, resource):
        return users
    if user.get("role") == "manager":
        uid = int(user["id"])
        return [u for u in users if int(u.get("managerId") or 0) == uid or int(u["id"]) == uid]
    return [u for u in users if int(u["id"]) == int(user["id"])]


def visible_names(user: dict[str, Any], resource: str | None = None) -> set[str] | None:
    """Ko'rish mumkin bo'lgan hodim ismlari. None = cheklovsiz."""
    if is_global(user, resource):
        return None
    return {u.get("name", "") for u in team_of(user, resource)}


def visible_ids(user: dict[str, Any], resource: str | None = None) -> set[int] | None:
    if is_global(user, resource):
        return None
    return {int(u["id"]) for u in team_of(user, resource)}


def owns(row: dict[str, Any], user: dict[str, Any], resource: str | None = None) -> bool:
    """Yozuv joriy foydalanuvchiga (yoki uning jamoasiga) tegishlimi."""
    names = visible_names(user, resource)
    if names is None:
        return True
    ids = visible_ids(user, resource) or set()
    owner_id = row.get("ownerId")
    if owner_id is not None and int(owner_id) in ids:
        return True
    return row.get("manager") in names


def scope_rows(
    rows: list[dict[str, Any]], user: dict[str, Any], resource: str | None = None
) -> list[dict[str, Any]]:
    """Ro'yxatni foydalanuvchi ko'rish doirasiga qisqartiradi."""
    if is_global(user, resource):
        return rows
    return [r for r in rows if owns(r, user, resource)]


def ensure_access(
    row: dict[str, Any] | None, user: dict[str, Any], resource: str | None = None
) -> dict[str, Any]:
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Yozuv topilmadi.")
    if not owns(row, user, resource):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Bu yozuv sizga tegishli emas.")
    return row