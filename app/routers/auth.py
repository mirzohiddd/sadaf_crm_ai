"""/api/auth — kirish, profil, parol."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from .. import storage
from ..deps import get_current_user, public_user
from ..schemas import LoginIn, PasswordChangeIn, ProfileIn, TokenOut
from ..security import create_token, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn) -> dict[str, Any]:
    login_name = body.login.strip().lower()
    user = next(
        (u for u in storage.read("users") if str(u.get("login", "")).lower() == login_name),
        None,
    )
    if not user or not verify_password(body.password, user.get("passwordHash", "")):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Login yoki parol noto'g'ri.")
    if not user.get("active", True):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Hisob bloklangan. Administratorga murojaat qiling.")

    storage.update("users", int(user["id"]), {"lastLoginAt": storage.now_iso()})
    return {
        "access_token": create_token(user),
        "token_type": "bearer",
        "user": public_user(user),
    }


@router.get("/me")
def me(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return public_user(user)


@router.post("/logout")
def logout(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    # JWT stateless — token frontendda o'chiriladi. Bu endpoint jurnallash uchun.
    return {"ok": True}


@router.put("/profile")
def update_profile(
    body: ProfileIn, user: dict[str, Any] = Depends(get_current_user)
) -> dict[str, Any]:
    """Har bir foydalanuvchi faqat o'z profilini o'zgartiradi."""
    patch = {
        k: v
        for k, v in body.model_dump(exclude_none=True).items()
        if k in ("name", "email", "phone", "address", "birthDate", "avatar")
    }
    if not patch:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "O'zgartirish uchun ma'lumot yo'q.")

    old_name = user.get("name")
    updated = storage.update("users", int(user["id"]), patch)

    # Ism o'zgarsa CRM yozuvlaridagi bog'lanishlar ham yangilanadi
    new_name = patch.get("name")
    if new_name and new_name != old_name:
        for collection, fields in (
            ("leads", ("manager",)),
            ("clients", ("manager",)),
            ("sales", ("manager",)),
            ("tasks", ("to", "from")),
        ):
            def _rename(rows: list[dict[str, Any]], flds=fields) -> None:
                for row in rows:
                    for field in flds:
                        if row.get(field) == old_name:
                            row[field] = new_name

            storage.mutate(collection, _rename)

    return public_user(updated or user)


@router.put("/password")
def change_password(
    body: PasswordChangeIn, user: dict[str, Any] = Depends(get_current_user)
) -> dict[str, Any]:
    if not verify_password(body.current, user.get("passwordHash", "")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Joriy parol noto'g'ri.")
    if body.current == body.next:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Yangi parol eskisidan farq qilsin.")

    storage.update("users", int(user["id"]), {"passwordHash": hash_password(body.next)})
    return {"ok": True, "message": "Parol yangilandi."}
