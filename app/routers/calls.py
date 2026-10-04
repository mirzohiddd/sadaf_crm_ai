"""AI Call Center API.

Mavjud konvensiyaga mos (``/api/leads/{id}/reminders`` + ``/api/reminders/{id}``
kabi):

- ``GET    /api/calls``                          — ruxsat doirasidagi qo'ng'iroqlar (arxiv)
- ``POST   /api/calls``                          — yangi qo'ng'iroq (lead uchun)
- ``GET    /api/calls/stats``                    — AI Center statistikasi
- ``GET    /api/calls/config``                   — STT/analiz sozlanganmi (kalitsiz!)
- ``GET    /api/calls/{id}``                     — qo'ng'iroq + transcript + analiz
- ``DELETE /api/calls/{id}``                     — o'chirish (qoidalar services/calls.py da)
- ``POST   /api/calls/{id}/recording``           — audio yuklash (multipart)
- ``GET    /api/calls/{id}/recording``           — audio tinglash (faqat ruxsat bilan)
- ``POST   /api/calls/{id}/transcribe``          — speech-to-text (Groq Whisper)
- ``GET    /api/calls/{id}/transcript``
- ``POST   /api/calls/{id}/transcript/swap-roles`` — ADMIN/MIJOZ ni almashtirish
- ``POST   /api/calls/{id}/analyze``             — AI analiz (Groq GPT-OSS)
- ``GET    /api/calls/{id}/analysis``
- ``GET    /api/leads/{lead_id}/calls``          — leadning qo'ng'iroqlar arxivi

Har bir endpoint: JWT autentifikatsiya (``require``) + qo'ng'iroq darajasida
ruxsat (``services.calls.get_call`` / ``get_call_for_write``).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse

from .. import config
from ..deps import require
from ..schemas import CallCreateIn
from ..services import calls as svc

router = APIRouter(tags=["calls"])


@router.get("/api/calls")
def list_calls(
    leadId: int | None = Query(None),
    managerId: int | None = Query(None),
    status_: str = Query("", alias="status"),
    q: str = Query(""),
    limit: int = Query(100, ge=1, le=500),
    user: dict[str, Any] = Depends(require("calls", "read")),
) -> list[dict[str, Any]]:
    return svc.list_calls(
        user, lead_id=leadId, manager_id=managerId, status_filter=status_, query=q, limit=limit
    )


@router.get("/api/calls/stats")
def call_stats(user: dict[str, Any] = Depends(require("calls", "read"))) -> dict[str, Any]:
    return svc.stats(user)


@router.get("/api/calls/config")
def call_config(user: dict[str, Any] = Depends(require("calls", "read"))) -> dict[str, Any]:
    """Frontend qaysi imkoniyat yoqilganini bilishi uchun. API kalit
    hech qachon qaytarilmaydi — faqat bor/yo'qligi."""
    provider = config.call_provider()
    return {
        "stt": config.call_stt_enabled(),
        "analysis": config.call_analysis_enabled(),
        "provider": provider or None,
        "sttModel": config.GROQ_STT_MODEL if provider == "groq" else (config.XAI_STT_MODEL or None),
        "analysisModel": config.GROQ_MODEL if provider == "groq" else (config.XAI_MODEL or None),
        # Groq STT fayl limiti (MB); undan katta audio saqlanadi, lekin transcript qilinmaydi.
        "maxSttMb": config.GROQ_STT_MAX_MB if provider == "groq" else None,
        "maxUploadMb": config.CALL_MAX_UPLOAD_MB,
        "channel": "browser_mic",
        "isBoss": svc.is_boss(user),
    }


@router.post("/api/calls", status_code=status.HTTP_201_CREATED)
def create_call(
    body: CallCreateIn, user: dict[str, Any] = Depends(require("calls", "write"))
) -> dict[str, Any]:
    lead = svc.get_visible_lead(body.leadId, user)
    created = svc.create_call(lead, user, consent=body.consent, direction=body.direction)
    return svc.public_call(created, user)


@router.get("/api/calls/{call_id}")
def get_call(call_id: int, user: dict[str, Any] = Depends(require("calls", "read"))) -> dict[str, Any]:
    return svc.call_detail(svc.get_call(call_id, user), user)


@router.delete("/api/calls/{call_id}")
def delete_call(call_id: int, user: dict[str, Any] = Depends(require("calls", "delete"))) -> dict[str, Any]:
    call = svc.get_call(call_id, user)
    svc.delete_call(call, user)
    return {"ok": True, "id": call_id}


@router.post("/api/calls/{call_id}/recording")
async def upload_recording(
    call_id: int,
    file: UploadFile = File(...),
    duration: float = Form(0),
    user: dict[str, Any] = Depends(require("calls", "write")),
) -> dict[str, Any]:
    call = svc.get_call_for_write(call_id, user)
    updated = await svc.save_recording(call, file, duration, user)
    return svc.public_call(updated, user)


@router.get("/api/calls/{call_id}/recording")
def download_recording(
    call_id: int, user: dict[str, Any] = Depends(require("calls", "read"))
) -> FileResponse:
    call = svc.get_call(call_id, user)
    path = svc.recording_path(call)
    if path is None or not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Audio fayl topilmadi.")
    mime = (call.get("recording") or {}).get("mime", "application/octet-stream")
    return FileResponse(
        path,
        media_type=mime,
        filename=f"call-{call_id}.{path.suffix.lstrip('.')}",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.post("/api/calls/{call_id}/transcribe")
async def transcribe(call_id: int, user: dict[str, Any] = Depends(require("calls", "write"))) -> dict[str, Any]:
    call = svc.get_call_for_write(call_id, user)
    await svc.transcribe_call(call, user)
    return svc.call_detail(svc.get_call(call_id, user), user)


@router.get("/api/calls/{call_id}/transcript")
def get_transcript(call_id: int, user: dict[str, Any] = Depends(require("calls", "read"))) -> dict[str, Any]:
    call = svc.get_call(call_id, user)
    transcript = svc.get_by_call("call_transcripts", int(call["id"]))
    if not transcript:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Transcript hali tayyor emas.")
    return transcript


@router.post("/api/calls/{call_id}/transcript/swap-roles")
def swap_roles(call_id: int, user: dict[str, Any] = Depends(require("calls", "write"))) -> dict[str, Any]:
    call = svc.get_call_for_write(call_id, user)
    svc.swap_roles(call)
    return svc.call_detail(svc.get_call(call_id, user), user)


@router.post("/api/calls/{call_id}/analyze")
async def analyze(call_id: int, user: dict[str, Any] = Depends(require("calls", "write"))) -> dict[str, Any]:
    call = svc.get_call_for_write(call_id, user)
    await svc.analyze_call(call, user)
    return svc.call_detail(svc.get_call(call_id, user), user)


@router.get("/api/calls/{call_id}/analysis")
def get_analysis(call_id: int, user: dict[str, Any] = Depends(require("calls", "read"))) -> dict[str, Any]:
    call = svc.get_call(call_id, user)
    analysis = svc.get_by_call("call_analyses", int(call["id"]))
    if not analysis:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "AI analiz hali tayyor emas.")
    return analysis


@router.get("/api/leads/{lead_id}/calls")
def lead_calls(lead_id: int, user: dict[str, Any] = Depends(require("calls", "read"))) -> list[dict[str, Any]]:
    """Leadning to'liq qo'ng'iroqlar arxivi (eng yangisi birinchi)."""
    svc.get_visible_lead(lead_id, user)
    return svc.list_calls(user, lead_id=lead_id, limit=500)
