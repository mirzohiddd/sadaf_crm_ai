"""Konfiguratsiya — barcha sozlamalar .env orqali beriladi."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")



def _dir_from_env(name: str, default: Path) -> Path:
    """Papka yo'lini env'dan oladi: bo'shliqlar tozalanadi, "~" ochiladi,
    nisbiy yo'l backend papkasiga nisbatan olinadi. Berilmagan bo'lsa — default."""
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    path = Path(raw).expanduser()
    return path if path.is_absolute() else BASE_DIR / path


# Render: Persistent Disk `/var/data` ga ulanadi va DATA_DIR=/var/data beriladi.
# Lokal development: DATA_DIR berilmaydi → backend/data ishlatiladi.
DATA_DIR = _dir_from_env("DATA_DIR", BASE_DIR / "data")
DATA_DIR.mkdir(parents=True, exist_ok=True)

EXPORT_DIR = DATA_DIR / "exports"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

# ——— Auth ———
DEFAULT_JWT_SECRET = "dev-secret-change-me"
JWT_SECRET = os.getenv("JWT_SECRET", DEFAULT_JWT_SECRET)
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "720"))

ADMIN_LOGIN = os.getenv("ADMIN_LOGIN", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
ADMIN_NAME = os.getenv("ADMIN_NAME", "Super Admin")
# Eski moslik: "menejer" / "12345" Super Admin hisobini avtomatik yaratish.
# Productionda "false" qiling (o'rniga ADMIN_LOGIN/ADMIN_PASSWORD ishlatiladi).
SEED_LEGACY_MENEJER = os.getenv("SEED_LEGACY_MENEJER", "true").strip().lower() not in ("0", "false", "no", "off")

# Lead round-robin: navbatga faol Menejerlar (admin) va faol Bosh menejer(lar)
# (super_admin) kiradi. Navbatdan chiqarish kerak bo'lgan hisoblar loginlari
# shu yerda vergul bilan (masalan ishlatilmaydigan eski "menejer" hisobi).
ROUND_ROBIN_EXCLUDE_LOGINS = {
    x.strip().lower() for x in os.getenv("ROUND_ROBIN_EXCLUDE_LOGINS", "").split(",") if x.strip()
}

# Xatolik tafsilotlarini (exception matni) API javobida ko'rsatish.
# Productionda o'chiq qoldiring — ichki ma'lumot tashqariga chiqmasin.
APP_DEBUG = os.getenv("APP_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")

# ——— CORS ———
CORS_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")
    if o.strip()
]

# ——— AI yordamchi (Groq) ———
# Kalit FAQAT .env orqali beriladi va faqat backendda turadi (frontendga
# hech qachon uzatilmaydi). GROQ_API_KEY bo'sh bo'lsa yordamchi oflayn
# (rule-based) rejimda ishlaydi — CRM to'xtamaydi.
#
# Standart model — Groq Free Tier'da mavjud, tool calling'ni qo'llaydigan
# production model. Groq modellar ro'yxati vaqti-vaqti bilan yangilanadi:
# https://console.groq.com/docs/models  |  https://console.groq.com/docs/deprecations
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip() or "openai/gpt-oss-120b"
# Asosiy model limitga (429) tushsa yoki vaqtincha ishlamasa shu model sinab ko'riladi.
# Bo'sh qoldirilsa zaxira model ishlatilmaydi.
GROQ_FALLBACK_MODEL = os.getenv("GROQ_FALLBACK_MODEL", "openai/gpt-oss-20b").strip()
GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1").strip().rstrip("/")
GROQ_TIMEOUT_SECONDS = float(os.getenv("GROQ_TIMEOUT_SECONDS", "45"))
# GPT-OSS (reasoning) modellari uchun fikrlash darajasi: low | medium | high.
# "low" — Free Tier token limitini tejaydi va javob tezroq keladi.
GROQ_REASONING_EFFORT = os.getenv("GROQ_REASONING_EFFORT", "low").strip().lower()
AI_MAX_TOKENS = int(os.getenv("AI_MAX_TOKENS", "1024"))
AI_TEMPERATURE = float(os.getenv("AI_TEMPERATURE", "0.3"))


def ai_enabled() -> bool:
    """Tashqi AI (Groq) ulangan-ulanmaganini bildiradi."""
    return bool(GROQ_API_KEY)


# ——— AI Call Center (xAI / Grok) ———
# Kalit va model FAQAT .env orqali beriladi. Kodda model nomi yo'q —
# XAI_MODEL bo'sh bo'lsa AI analiz o'chiq hisoblanadi va foydalanuvchiga
# tushunarli xabar qaytariladi (recording/transcript baribir saqlanadi).
XAI_API_KEY = os.getenv("XAI_API_KEY", "").strip()
XAI_MODEL = os.getenv("XAI_MODEL", "").strip()
XAI_BASE_URL = os.getenv("XAI_BASE_URL", "https://api.x.ai/v1").strip().rstrip("/")
# Speech-to-text modeli. Bo'sh bo'lsa xAI o'zining standart modelini ishlatadi.
XAI_STT_MODEL = os.getenv("XAI_STT_MODEL", "").strip()
# STT uchun til kodi (masalan "ru"). Bo'sh = avtomatik aniqlash.
XAI_STT_LANGUAGE = os.getenv("XAI_STT_LANGUAGE", "").strip()
XAI_TIMEOUT_SECONDS = float(os.getenv("XAI_TIMEOUT_SECONDS", "180"))

# Qo'ng'iroq yozuvlari (audio) saqlanadigan papka. Hech qachon statik
# (ochiq) papka sifatida berilmaydi — fayl faqat autentifikatsiya va
# ruxsat tekshiruvidan keyin /api/calls/{id}/recording orqali uzatiladi.
RECORDINGS_DIR = _dir_from_env("RECORDINGS_DIR", DATA_DIR / "recordings")
RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
CALL_MAX_UPLOAD_MB = int(os.getenv("CALL_MAX_UPLOAD_MB", "100"))


def xai_stt_enabled() -> bool:
    """Speech-to-text uchun faqat kalit kerak (model ixtiyoriy)."""
    return bool(XAI_API_KEY)


def xai_analysis_enabled() -> bool:
    """Grok analiz uchun kalit VA model .env da berilgan bo'lishi shart."""
    return bool(XAI_API_KEY and XAI_MODEL)


# ——— AI Call Center (Groq) — asosiy provayder ———
# Mavjud GROQ_API_KEY ishlatiladi (yangi kalit kerak emas).
# Oqim: audio → Groq Whisper (STT) → transcript → GROQ_MODEL (analiz).
GROQ_STT_MODEL = os.getenv("GROQ_STT_MODEL", "whisper-large-v3-turbo").strip() or "whisper-large-v3-turbo"
# Til kodi (ISO-639-1), masalan "uz". Bo'sh = avtomatik aniqlash.
GROQ_STT_LANGUAGE = os.getenv("GROQ_STT_LANGUAGE", "uz").strip()
# Groq Free Tier STT fayl limiti — 25 MB (Developer tarifda 100 MB).
GROQ_STT_MAX_MB = int(os.getenv("GROQ_STT_MAX_MB", "25"))
GROQ_STT_TIMEOUT_SECONDS = float(os.getenv("GROQ_STT_TIMEOUT_SECONDS", "180"))
# Qo'ng'iroq analizi javobi uchun token limiti va modelga yuboriladigan
# transcript hajmi (Free Tier: ~8K token/daqiqa — shunga moslangan).
GROQ_CALL_MAX_TOKENS = int(os.getenv("GROQ_CALL_MAX_TOKENS", "2500"))
GROQ_CALL_TRANSCRIPT_CHARS = int(os.getenv("GROQ_CALL_TRANSCRIPT_CHARS", "10000"))


def call_provider() -> str:
    """Call Center qaysi provayderda ishlaydi: groq | xai | "" (sozlanmagan).

    GROQ_API_KEY bo'lsa — doim Groq. xAI faqat eski moslik uchun (Groq
    kaliti yo'q, lekin XAI_API_KEY berilgan bo'lsa) ishlatiladi.
    """
    if GROQ_API_KEY:
        return "groq"
    if XAI_API_KEY:
        return "xai"
    return ""


def call_stt_enabled() -> bool:
    return call_provider() == "groq" or xai_stt_enabled()


def call_analysis_enabled() -> bool:
    return call_provider() == "groq" or xai_analysis_enabled()


# ——— Google Sheets → CRM lead integratsiyasi ———
# Google Sheetsdagi Apps Script shu "maxfiy kalit"ni har bir so'rovda
# yuborishi kerak (header: X-Sheets-Secret). Productionda albatta
# o'zgartiring — aks holda istalgan kishi soxta lead yubora oladi.
SHEETS_WEBHOOK_SECRET = os.getenv("SHEETS_WEBHOOK_SECRET", "dev-sheets-secret-change-me")
# Sheetdan `source` maydoni kelmasa shu qiymat ishlatiladi.
SHEETS_DEFAULT_SOURCE = os.getenv("SHEETS_DEFAULT_SOURCE", "Google Sheets")


# ——— Lead Form (sadaf-landing) → CRM lead integratsiyasi ———
# Landing sahifadagi ariza formasi shu "maxfiy kalit"ni har bir so'rovda
# yuborishi kerak (header: X-Website-Secret). Bu, kim CRM'ga soxta lead
# yubormasligi uchun eng oddiy himoya — chunki forma login qilmagan
# tashrifchi tomonidan to'ldiriladi va JWT talab qilib bo'lmaydi.
# Productionda albatta o'zgartiring va buni frontenddagi
# VITE_LEAD_API_KEY bilan bir xil qiymatga sozlang.
WEBSITE_WEBHOOK_SECRET = os.getenv("WEBSITE_WEBHOOK_SECRET", "dev-website-secret-change-me")
# Lead Form'dan `source` maydoni kelmasa shu qiymat ishlatiladi.
WEBSITE_DEFAULT_SOURCE = os.getenv("WEBSITE_DEFAULT_SOURCE", "Veb-sayt")