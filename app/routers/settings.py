"""/api/settings — har bir account uchun alohida ko'rinish sozlamalari."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from .. import storage
from ..deps import get_current_user
from ..schemas import SettingsIn
from ..seed import default_settings, ensure_settings

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def get_settings(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """Faqat joriy foydalanuvchi sozlamasi. Boshqa hodimga ta'sir qilmaydi."""
    return ensure_settings(int(user["id"]))


@router.put("")
def save_settings(
    body: SettingsIn, user: dict[str, Any] = Depends(get_current_user)
) -> dict[str, Any]:
    row = ensure_settings(int(user["id"]))
    patch = {k: v for k, v in body.model_dump().items() if k not in ("id", "userId")}

    # appearance qisman kelsa — mavjudi bilan birlashtiramiz
    if isinstance(patch.get("appearance"), dict):
        merged = {**(row.get("appearance") or {}), **patch["appearance"]}
        patch["appearance"] = merged

    return storage.update("settings", int(row["id"]), patch) or row


@router.post("/reset")
def reset_settings(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    row = ensure_settings(int(user["id"]))
    fresh = default_settings(int(user["id"]))
    return storage.update("settings", int(row["id"]), fresh) or row
