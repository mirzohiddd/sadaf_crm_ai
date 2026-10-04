from __future__ import annotations
import asyncio
import logging
import contextlib
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app import seed
from app.config import (
    APP_DEBUG,
    CORS_ORIGINS,
    DEFAULT_JWT_SECRET,
    GROQ_FALLBACK_MODEL,
    GROQ_MODEL,
    GROQ_STT_LANGUAGE,
    GROQ_STT_MODEL,
    JWT_SECRET,
    ai_enabled,
    call_analysis_enabled,
    call_provider,
    call_stt_enabled,
    xai_analysis_enabled,
)
from app.routers import (
    ai,
    attendance,
    auth,
    calls,
    crud,
    employees,
    insights,
    integrations,
    leads,
    notifications,
    reminders,
    reports,
    settings,
    tasks,
)
from app.security import decode_token
from app.services import reminders as reminders_service
from app.services.realtime import manager as realtime_manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # har bir tashqi so'rovni log qilmasin
logger = logging.getLogger("sadaf")
@asynccontextmanager
async def lifespan(_: FastAPI):
    seed.run()
    realtime_manager.bind_loop(asyncio.get_running_loop())
    # Lead eslatmalari uchun fon vazifasi: davriy ravishda vaqti kelgan
    # eslatmalarni topib, bildirishnoma yuboradi (pastda check_due()).
    reminder_task = asyncio.create_task(reminders_service.run_forever())
    if ai_enabled():
        fb = f", zaxira: {GROQ_FALLBACK_MODEL}" if GROQ_FALLBACK_MODEL else ""
        print(f"[sadaf] AI yordamchi: Groq ({GROQ_MODEL}{fb})")
    else:
        print("[sadaf] AI yordamchi: offline (rule-based) — GROQ_API_KEY berilmagan")
    if JWT_SECRET == DEFAULT_JWT_SECRET or len(JWT_SECRET) < 32:
        print("[sadaf] OGOHLANTIRISH: JWT_SECRET standart yoki juda qisqa. Productionda "
              "`openssl rand -hex 32` bilan yangi kalit yarating!")
    print("[sadaf] Real vaqtli sinxronizatsiya (WebSocket): yoqilgan")
    print("[sadaf] Lead eslatmalari: fon vazifasi yoqilgan (har 20s tekshiradi)")
    provider = call_provider()
    if provider == "groq":
        print(f"[sadaf] AI Call Center: Groq — STT={GROQ_STT_MODEL} (til: {GROQ_STT_LANGUAGE or 'avto'}), "
              f"analiz={GROQ_MODEL}")
    elif provider == "xai":
        grok_state = "yoqilgan" if xai_analysis_enabled() else "o'chiq (XAI_MODEL yo'q)"
        print(f"[sadaf] AI Call Center: xAI — STT=yoqilgan, Grok analiz={grok_state}")
    else:
        print("[sadaf] AI Call Center: STT va AI analiz o'chiq (GROQ_API_KEY yo'q) — faqat audio yoziladi")
    yield
    reminder_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await reminder_task


app = FastAPI(
    title="SADAF CRM API",
    version="1.0.0",
    description="Turagentlik CRM backendi. Barcha ma'lumot JSON fayllarda saqlanadi.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_origin_regex=r"https://.*\.vercel\.app|http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)
for router in (
    auth.router,
    employees.router,
    leads.router,
    crud.clients_router,
    crud.tours_router,
    crud.sales_router,
    tasks.router,
    notifications.router,
    reminders.router,
    attendance.router,
    insights.dashboard_router,
    insights.analytics_router,
    insights.activity_router,
    reports.router,
    settings.router,
    ai.router,
    integrations.router,
    calls.router,
):
    app.include_router(router)


@app.get("/api/health", tags=["system"])
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "ai": "groq" if ai_enabled() else "local",
        "callCenter": {
            "stt": call_stt_enabled(),
            "analysis": call_analysis_enabled(),
            "provider": call_provider() or None,
        },
    }


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket, token: str = "") -> None:
    """Real vaqtli yangilanishlar kanali.

    Frontend login qilgach shu kanalga ulanadi. Har safar biror hodim
    biror ma'lumotni (masalan vazifa) o'zgartirsa — barcha ulangan
    mijozlarga zudlik bilan signal boradi va ular tegishli bo'limni
    qayta yuklaydi.
    """
    payload = decode_token(token) if token else None
    if not payload:
        await websocket.close(code=4401)
        return

    user_id = int(payload.get("sub", 0)) or None
    await realtime_manager.connect(websocket, user_id)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        realtime_manager.disconnect(websocket)
    except Exception:  # noqa: BLE001
        realtime_manager.disconnect(websocket)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    """Kutilmagan xato butun CRM ni yiqitmasin. Tafsilot faqat server
    logiga yoziladi; foydalanuvchiga (APP_DEBUG o'chiq bo'lsa) umumiy xabar."""
    logger.exception("Kutilmagan xato: %s %s", request.method, request.url.path)
    detail = f"Serverda kutilmagan xato: {exc}" if APP_DEBUG else "Serverda kutilmagan xato. Keyinroq qayta urinib ko'ring."
    return JSONResponse(status_code=500, content={"detail": detail})