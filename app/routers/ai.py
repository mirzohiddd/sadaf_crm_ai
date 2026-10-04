"""/api/ai — CRM ichidagi AI yordamchi."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from .. import storage
from .. import config
from ..deps import get_current_user
from ..schemas import AiChatIn
from ..services import ai

router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.get("/status")
def status(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    enabled = config.ai_enabled()
    return {
        "enabled": enabled,
        "provider": "groq" if enabled else "local",
        "model": config.GROQ_MODEL if enabled else "offline",
        "fallbackModel": (config.GROQ_FALLBACK_MODEL or None) if enabled else None,
        "mode": "groq" if enabled else "local",
        "tools": list(ai.TOOL_NAMES),
    }


@router.post("/chat")
async def chat(body: AiChatIn, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """AI javob beradi. Barcha CRM qidiruvlari joriy user huquqi doirasida."""
    answer = await ai.chat(body.message, body.history, user)
    ai.save_history(int(user["id"]), body.message, answer)
    return answer


@router.get("/history")
def history(
    limit: int = Query(30, ge=1, le=200), user: dict[str, Any] = Depends(get_current_user)
) -> list[dict[str, Any]]:
    uid = int(user["id"])
    rows = [c for c in storage.read("ai_chats") if int(c.get("userId", 0)) == uid]
    return rows[:limit]


@router.delete("/history")
def clear_history(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    uid = int(user["id"])
    storage.write(
        "ai_chats", [c for c in storage.read("ai_chats") if int(c.get("userId", 0)) != uid]
    )
    return {"ok": True}
