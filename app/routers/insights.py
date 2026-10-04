"""/api/dashboard, /api/analytics, /api/activity — hisoblangan ko'rsatkichlar."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from .. import storage
from ..deps import get_current_user, is_global, require, visible_names
from ..schemas import AnalyticsGoalIn
from ..seed import ensure_analytics_goal
from ..services import notify
from ..services.crm import get_analytics, get_dashboard_stats, get_employee_stats

dashboard_router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
analytics_router = APIRouter(prefix="/api/analytics", tags=["analytics"])
activity_router = APIRouter(prefix="/api/activity", tags=["activity"])


@dashboard_router.get("")
def dashboard(
    period: str = Query("month", pattern="^(today|week|month)$"),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Har bir account uchun alohida dashboard.

    Operator — faqat o'z statistikasi, menejer — jamoasi,
    admin/super admin — barcha ma'lumot.
    """
    return get_dashboard_stats(user, period)


@dashboard_router.get("/me")
def my_stats(
    name: str = "", user: dict[str, Any] = Depends(get_current_user)
) -> dict[str, Any]:
    return get_employee_stats(user, name)


@analytics_router.get("")
def analytics(user: dict[str, Any] = Depends(require("analytics", "read"))) -> dict[str, Any]:
    return get_analytics(user)


# ——— Oylik maqsad (Analitika sahifasidagi "Oylik maqsad" kartasi) ———
#
# Butun kompaniya uchun bitta qiymat, backendda analytics_goals.json faylida
# saqlanadi (localStorage EMAS). Har kim o'qiy oladi (Analitikani ko'ra
# oladigan har bir rol), lekin faqat Super Admin o'zgartira oladi.


@analytics_router.get("/goal")
def get_goal(user: dict[str, Any] = Depends(require("analytics", "read"))) -> dict[str, Any]:
    return ensure_analytics_goal()


@analytics_router.put("/goal")
def save_goal(
    body: AnalyticsGoalIn, user: dict[str, Any] = Depends(require("analytics", "read"))
) -> dict[str, Any]:
    if user.get("role") != "super_admin":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Oylik maqsadni faqat Super Admin o'zgartira oladi."
        )
    row = ensure_analytics_goal()
    patch = {
        "monthlyTarget": body.monthlyTarget,
        "updatedBy": int(user["id"]),
        "updatedByName": user.get("name", ""),
    }
    return storage.update("analytics_goals", int(row["id"]), patch) or row


@activity_router.get("")
def activity(
    limit: int = Query(60, ge=1, le=500), user: dict[str, Any] = Depends(get_current_user)
) -> list[dict[str, Any]]:
    """Faoliyat jurnali. Admin hammasini, qolganlar o'z jamoasinikini ko'radi."""
    rows = storage.read("activity")
    if not is_global(user):
        names = visible_names(user) or set()
        rows = [r for r in rows if r.get("actor") in names]
    return [{**r, "text": notify.activity_text(r)} for r in rows[:limit]]