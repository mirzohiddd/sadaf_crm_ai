"""Parol hashlash, JWT token va rol-permission matritsasi."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from .config import JWT_ALGORITHM, JWT_EXPIRE_MINUTES, JWT_SECRET

_ITERATIONS = 200_000


# ——— Parol ———


def hash_password(password: str) -> str:
    """PBKDF2-SHA256. Natija: pbkdf2_sha256$iterations$salt$hash"""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), _ITERATIONS
    ).hex()
    return f"pbkdf2_sha256${_ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt, digest = (stored or "").split("$")
        if algo != "pbkdf2_sha256":
            return False
        check = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt.encode("utf-8"), int(iterations)
        ).hex()
        return hmac.compare_digest(check, digest)
    except (ValueError, AttributeError):
        return False


# ——— Token ———


def create_token(user: dict[str, Any]) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user["id"]),
        "login": user["login"],
        "role": user["role"],
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=JWT_EXPIRE_MINUTES)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None


# ——— Rollar ———

ROLES = ("super_admin", "admin", "manager", "operator")

ROLE_LABELS = {
    "super_admin": "Super Admin",
    "admin": "Admin",
    "manager": "Menejer",
    "operator": "Operator",
}

# Har bir rol uchun ruxsat etilgan sahifalar (frontend navbar shu ro'yxatga qaraydi)
ROLE_PAGES: dict[str, list[str]] = {
    "super_admin": [
        "dashboard", "hodimlar", "turlar", "ledlar", "mijozlar",
        "savdolar", "analitika", "hisobot", "vazifalar",
        "bildirishnomalar", "ai", "sozlamalar",
    ],
    # Admin roli faqat quyidagi sahifalarni ko'radi: Dashboard, Ledlar,
    # Mijozlar, Savdolar, Hisobot, Sozlamalar (Hodimlar/Turlar/Analitika yopiq).
    "admin": [
        "dashboard", "ledlar", "mijozlar", "savdolar", "hisobot",
        "vazifalar", "bildirishnomalar", "ai", "sozlamalar",
    ],
    "manager": [
        "dashboard", "hodimlar", "turlar", "ledlar", "mijozlar",
        "savdolar", "analitika", "hisobot", "vazifalar",
        "bildirishnomalar", "ai", "sozlamalar",
    ],
    "operator": [
        "dashboard", "ledlar", "mijozlar", "vazifalar",
        "bildirishnomalar", "ai", "sozlamalar",
    ],
}

# resurs -> amal -> ruxsat berilgan rollar
PERMISSIONS: dict[str, dict[str, tuple[str, ...]]] = {
    "employees": {
        "read": ("super_admin", "admin", "manager"),
        "write": ("super_admin", "admin"),
        "delete": ("super_admin",),
    },
    "leads": {
        "read": ROLES,
        "write": ROLES,
        "delete": ("super_admin", "admin", "manager"),
    },
    "clients": {
        "read": ROLES,
        "write": ROLES,
        "delete": ("super_admin", "admin", "manager"),
    },
    "tours": {
        "read": ROLES,
        "write": ("super_admin", "admin", "manager"),
        "delete": ("super_admin", "admin"),
    },
    "sales": {
        "read": ROLES,
        "write": ("super_admin", "admin", "manager"),
        "delete": ("super_admin", "admin"),
    },
    "tasks": {"read": ROLES, "write": ROLES, "delete": ROLES},
    "analytics": {"read": ("super_admin", "admin", "manager")},
    "reports": {"read": ("super_admin", "admin", "manager")},
    "attendance": {"read": ROLES, "write": ROLES},
    "settings": {"read": ROLES, "write": ROLES},
    # AI Call Center. Bu yerdagi ruxsat — faqat "umuman ishlata oladimi".
    # Qaysi qo'ng'iroqni ko'ra olishi services/calls.py da alohida
    # (manager isolation) tekshiriladi.
    "calls": {
        "read": ROLES,
        "write": ROLES,
        "delete": ROLES,
    },
}

# Bu rollar butun tizim ma'lumotini ko'radi
GLOBAL_ROLES = ("super_admin", "admin")


def can(role: str, resource: str, action: str = "read") -> bool:
    allowed = PERMISSIONS.get(resource, {}).get(action)
    return bool(allowed) and role in allowed


def pages_for(role: str) -> list[str]:
    return ROLE_PAGES.get(role, ROLE_PAGES["operator"])


def permissions_for(role: str) -> dict[str, list[str]]:
    """Frontend uchun tekis permission xaritasi."""
    out: dict[str, list[str]] = {}
    for resource, actions in PERMISSIONS.items():
        out[resource] = [a for a, roles in actions.items() if role in roles]
    return out
