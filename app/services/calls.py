"""AI Call Center — biznes mantiq.

Oqim:  Lead → Manager → Call → Recording → Transcript → AI Analysis → Archive

AI provayder (``config.call_provider()``):
- ``groq`` (asosiy) — GROQ_API_KEY: Groq Whisper (STT) + GROQ_MODEL (analiz).
- ``xai``  (eski moslik) — faqat GROQ_API_KEY yo'q va XAI_API_KEY berilgan bo'lsa.

Ma'lumotlar (JSON kolleksiyalar, mavjud ``storage`` qatlami orqali):

- ``calls``            — har bir qo'ng'iroq ALOHIDA yozuv (lead, menejer,
                         vaqt, davomiylik, audio metadata, statuslar).
- ``call_transcripts`` — callId bo'yicha 1:1 transcript (ADMIN / MIJOZ segmentlari).
- ``call_analyses``    — callId bo'yicha 1:1 AI analiz natijasi.
- Audio fayllar        — ``RECORDINGS_DIR`` (standart ``data/recordings``),
                         tasodifiy (uuid) nom bilan; hech qachon ochiq URL yo'q.

RUXSAT (manager isolation) — FAQAT backendda, foydalanuvchi tokenidan:

- Bosh menejer (``super_admin``)   — barcha qo'ng'iroqlar.
- Boshqa rol (menejer = ``admin``) — (a) o'zi qilgan qo'ng'iroqlar, yoki
  (b) unga hozir ko'rinadigan (``deps.owns``) leadlarning qo'ng'iroqlari.
  Frontend yuborgan ``managerId`` hech qachon hisobga olinmaydi —
  qo'ng'iroq egasi doim token egasi bo'ladi.

Kelajakdagi VoIP integratsiyasi: ``channel`` maydoni (hozir
``browser_mic``) va ``externalId`` maydoni VoIP provayderi (masalan
Asterisk/Twilio) qo'ng'iroq ID sini saqlash uchun ajratilgan. VoIP webhook
yozuvni yaratib, audio faylni ``store_recording_bytes()`` orqali
saqlashi kifoya — transcript/analiz/arxiv/statistika o'zgarishsiz ishlaydi.
"""
from __future__ import annotations

import hashlib
import logging
import os
import uuid
import wave
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile, status

from .. import config, storage
from ..deps import is_global, owns, scope_rows
from ..security import ROLE_LABELS
from . import groq_service, notify, xai_service
from .groq_service import GroqError
from .xai_service import XaiError

# Ikkala provayder xatosi ham foydalanuvchiga ko'rsatsa bo'ladigan ``.message`` ga ega.
AiError = (XaiError, GroqError)

# Provayder xatosi → HTTP status. Sozlanmagan xizmat 502 (Bad Gateway) EMAS:
# tashqi server javob bermagani yo'q, shunchaki .env da kalit yo'q.
_ERROR_STATUS = {
    "groq_not_configured": status.HTTP_503_SERVICE_UNAVAILABLE,
    "xai_not_configured": status.HTTP_503_SERVICE_UNAVAILABLE,
    "ai_not_configured": status.HTTP_503_SERVICE_UNAVAILABLE,
    "groq_too_large": status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
    "xai_too_large": status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
    "groq_rate_limit": status.HTTP_429_TOO_MANY_REQUESTS,
    "xai_rate_limit": status.HTTP_429_TOO_MANY_REQUESTS,
    "empty_transcript": status.HTTP_422_UNPROCESSABLE_ENTITY,
    "audio_read_error": status.HTTP_500_INTERNAL_SERVER_ERROR,
}


def _ai_http_error(exc: Exception) -> HTTPException:
    code = getattr(exc, "code", "")
    return HTTPException(_ERROR_STATUS.get(code, status.HTTP_502_BAD_GATEWAY), getattr(exc, "message", str(exc)))


def _not_configured(what: str) -> GroqError:
    return GroqError(
        f"{what} sozlanmagan: backend .env faylga GROQ_API_KEY qo'shing.", "ai_not_configured"
    )


async def _stt(path: Path, mime: str, keyterms: list[str]) -> tuple[dict[str, Any], str, str]:
    """Audio → matn. Qaytaradi: (stt, provider, model)."""
    provider = config.call_provider()
    if provider == "groq":
        prompt = "SADAF turagentligi telefon suhbati. " + ", ".join(keyterms)
        stt = await groq_service.transcribe(path, path.name, mime, prompt=prompt)
        return stt, "groq", config.GROQ_STT_MODEL
    if provider == "xai":
        stt = await xai_service.transcribe(path, path.name, mime, keyterms=keyterms)
        return stt, "xai", config.XAI_STT_MODEL or "xai-default"
    raise _not_configured("Speech-to-text")


async def _chat_json(
    system: str, user: str, schema: dict[str, Any], name: str, *, temperature: float = 0.2, **groq_kw: Any
) -> dict[str, Any]:
    if config.call_provider() == "groq":
        return await groq_service.chat_json(system, user, schema, name, temperature=temperature, **groq_kw)
    if config.xai_analysis_enabled():
        return await xai_service.chat_json(system, user, schema, name, temperature=temperature)
    raise _not_configured("AI analiz")


def _analysis_model() -> str:
    return config.GROQ_MODEL if config.call_provider() == "groq" else config.XAI_MODEL

RESOURCE = "calls"
logger = logging.getLogger("sadaf.calls")

# Bitta jarayon (transcribe/analyze) "osilib" qolsa — shu vaqtdan keyin
# qayta ishga tushirishga ruxsat beriladi.
PROCESSING_LOCK = timedelta(minutes=10)
MIN_AUDIO_BYTES = 1024
MAX_DURATION_SECONDS = 4 * 3600
CHUNK = 1024 * 1024
MAX_TRANSCRIPT_CHARS = 60_000

RATING_LABELS = {
    "excellent": "A'lo",
    "good": "Yaxshi",
    "average": "O'rtacha",
    "poor": "Yomon",
}
OPPORTUNITY_LABELS = {"high": "Yuqori", "medium": "O'rtacha", "low": "Past"}

COMMUNICATION_ITEMS: list[tuple[str, str]] = [
    ("greeting", "Salomlashish"),
    ("respect", "Hurmat"),
    ("clarity", "Aniq gapirish"),
    ("answered_questions", "Mijoz savollariga javob"),
    ("manners", "Muomala madaniyati"),
    ("no_rude_language", "Nomaqbul / qo'pol iboralar yo'q"),
]
SALES_ITEMS: list[tuple[str, str]] = [
    ("needs", "Mijoz ehtiyojini aniqlash"),
    ("budget", "Budjetni aniqlash"),
    ("interest", "Qiziqishni aniqlash"),
    ("product_explained", "Mahsulot / tur haqida tushuntirish"),
    ("price_explained", "Narxni tushuntirish"),
    ("benefits_explained", "Afzalliklarni tushuntirish"),
    ("objection_handling", "E'tirozlar bilan ishlash"),
    ("cta", "CTA (aniq taklif / harakatga chaqiruv)"),
    ("follow_up", "Follow-up (keyingi aloqa kelishilgan)"),
]


# ——————————————————————————————————————————————————————————
#  Yordamchilar
# ——————————————————————————————————————————————————————————


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _is_locked(started_at: Any) -> bool:
    started = _parse_iso(started_at)
    return bool(started and _now() - started < PROCESSING_LOCK)


def _uid(user: dict[str, Any]) -> int:
    return int(user["id"])


def get_by_call(collection: str, call_id: int) -> dict[str, Any] | None:
    for row in storage.read(collection):
        if int(row.get("callId", 0)) == int(call_id):
            return row
    return None


def _upsert_by_call(collection: str, call_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """callId bo'yicha 1:1 yozuvni yaratadi yoki almashtiradi (bitta lock ichida)."""

    def _do(rows: list[dict[str, Any]]) -> dict[str, Any]:
        for row in rows:
            if int(row.get("callId", 0)) == int(call_id):
                version = int(row.get("version") or 1) + 1
                keep_id, created_at = row.get("id"), row.get("createdAt")
                row.clear()
                row.update(payload)
                row["id"] = keep_id or storage.next_id(rows)
                row["createdAt"] = created_at or storage.now_iso()
                row["callId"] = int(call_id)
                row["version"] = version
                row["updatedAt"] = storage.now_iso()
                return dict(row)
        row = dict(payload)
        row["id"] = storage.next_id(rows)
        row["callId"] = int(call_id)
        row["version"] = 1
        row["createdAt"] = storage.now_iso()
        row["updatedAt"] = row["createdAt"]
        rows.insert(0, row)
        return dict(row)

    return storage.mutate(collection, _do)


def _delete_by_call(collection: str, call_id: int) -> None:
    rows = storage.read(collection)
    kept = [r for r in rows if int(r.get("callId", 0)) != int(call_id)]
    if len(kept) != len(rows):
        storage.write(collection, kept)


# ——————————————————————————————————————————————————————————
#  Ruxsat (manager isolation)
# ——————————————————————————————————————————————————————————


def is_boss(user: dict[str, Any]) -> bool:
    """Bosh menejer — barcha qo'ng'iroqlarni ko'radi."""
    return is_global(user, RESOURCE)


def visible_lead_ids(user: dict[str, Any]) -> set[int]:
    return {int(lead["id"]) for lead in scope_rows(storage.read("leads"), user, resource="leads")}


def can_view(call: dict[str, Any], user: dict[str, Any], lead_ids: set[int] | None = None) -> bool:
    if is_boss(user):
        return True
    if int(call.get("managerId") or 0) == _uid(user):
        return True
    if lead_ids is None:
        lead = storage.get_one("leads", int(call.get("leadId") or 0))
        return lead is not None and owns(lead, user, "leads")
    return int(call.get("leadId") or 0) in lead_ids


def can_modify(call: dict[str, Any], user: dict[str, Any]) -> bool:
    """Audio yuklash, transcript/analizni ishga tushirish — faqat qo'ng'iroq
    egasi (uni qilgan menejer) yoki Bosh menejer."""
    return is_boss(user) or int(call.get("managerId") or 0) == _uid(user)


def get_call(call_id: int, user: dict[str, Any]) -> dict[str, Any]:
    call = storage.get_one("calls", call_id)
    if not call:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Qo'ng'iroq topilmadi.")
    if not can_view(call, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Bu qo'ng'iroq sizga tegishli emas.")
    return call


def get_call_for_write(call_id: int, user: dict[str, Any]) -> dict[str, Any]:
    call = get_call(call_id, user)
    if not can_modify(call, user):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Bu qo'ng'iroqni faqat uni qilgan menejer yoki Bosh menejer o'zgartira oladi.",
        )
    return call


def get_visible_lead(lead_id: int, user: dict[str, Any]) -> dict[str, Any]:
    lead = storage.get_one("leads", lead_id)
    if not lead:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lead topilmadi.")
    if not owns(lead, user, "leads"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Bu lead sizga biriktirilmagan.")
    return lead


def visible_calls(user: dict[str, Any]) -> list[dict[str, Any]]:
    rows = storage.read("calls")
    if is_boss(user):
        return rows
    lead_ids = visible_lead_ids(user)
    return [c for c in rows if can_view(c, user, lead_ids)]


# ——————————————————————————————————————————————————————————
#  Tashqariga chiqarish (fayl yo'li va ichki maydonlarsiz)
# ——————————————————————————————————————————————————————————


def public_call(call: dict[str, Any], user: dict[str, Any] | None = None) -> dict[str, Any]:
    out = {k: v for k, v in call.items() if k not in ("recording", "processing")}
    rec = call.get("recording") or None
    out["recording"] = (
        {
            "available": True,
            "mime": rec.get("mime", ""),
            "size": int(rec.get("size") or 0),
            "uploadedAt": rec.get("uploadedAt", ""),
        }
        if rec
        else None
    )
    if user is not None:
        out["canModify"] = can_modify(call, user)
    return out


def call_detail(call: dict[str, Any], user: dict[str, Any]) -> dict[str, Any]:
    out = public_call(call, user)
    out["transcript"] = get_by_call("call_transcripts", int(call["id"]))
    out["analysis"] = get_by_call("call_analyses", int(call["id"]))
    return out


def list_calls(
    user: dict[str, Any],
    *,
    lead_id: int | None = None,
    manager_id: int | None = None,
    status_filter: str = "",
    query: str = "",
    limit: int = 100,
) -> list[dict[str, Any]]:
    # Avval ruxsat doirasi, keyin filtr — shuning uchun managerId parametri
    # bilan boshqa menejer ma'lumotini olib bo'lmaydi.
    rows = visible_calls(user)
    if lead_id is not None:
        rows = [c for c in rows if int(c.get("leadId") or 0) == int(lead_id)]
    if manager_id is not None and is_boss(user):
        rows = [c for c in rows if int(c.get("managerId") or 0) == int(manager_id)]
    if status_filter:
        if status_filter == "pending_analysis":
            rows = [c for c in rows if c.get("recording") and c.get("analysisStatus") != "done"]
        elif status_filter == "problems":
            rows = [c for c in rows if int((c.get("analysisSummary") or {}).get("problems") or 0) > 0]
        else:
            rows = [c for c in rows if c.get("status") == status_filter]
    q = query.strip().lower()
    if q:
        rows = [
            c for c in rows
            if q in str(c.get("leadName", "")).lower()
            or q in str(c.get("leadPhone", "")).lower()
            or q in str(c.get("managerName", "")).lower()
        ]
    rows = sorted(rows, key=lambda c: str(c.get("createdAt", "")), reverse=True)
    return [public_call(c, user) for c in rows[: max(1, min(limit, 500))]]


# ——————————————————————————————————————————————————————————
#  1. Qo'ng'iroq yaratish
# ——————————————————————————————————————————————————————————


def create_call(lead: dict[str, Any], user: dict[str, Any], *, consent: bool, direction: str) -> dict[str, Any]:
    if not consent:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Yozib olishdan oldin mijozni ogohlantirib, rozilik olinganini tasdiqlang.",
        )
    uid = _uid(user)
    record = {
        "leadId": int(lead["id"]),
        # Lead holatining qo'ng'iroq paytidagi nusxasi — lead keyin
        # o'zgarsa/o'chsa ham arxiv o'qiladigan bo'lib qoladi.
        "leadName": lead.get("name", ""),
        "leadPhone": lead.get("phone", ""),
        "leadPlatform": lead.get("platform") or lead.get("source", ""),
        "leadStage": lead.get("stage", ""),
        "leadManager": lead.get("manager", ""),
        # Egasi DOIM token egasi — frontend yuborgan qiymat ishlatilmaydi.
        "managerId": uid,
        "managerName": user.get("name", ""),
        "channel": "browser_mic",
        "direction": direction,
        "externalId": None,
        "status": "created",
        "consent": {"confirmed": True, "at": storage.now_iso(), "by": uid},
        "duration": 0,
        "recording": None,
        "transcriptStatus": "pending",
        "transcriptError": "",
        "analysisStatus": "pending",
        "analysisError": "",
        "analysisSummary": None,
        "analysisOutdated": False,
        "date": storage.today_uz(),
        "time": storage.now_time(),
    }
    created = storage.insert("calls", record)
    notify.log(user.get("name", ""), "create", "call", lead.get("name", ""),
               {"callId": created["id"]}, actor_id=uid)
    return created


def delete_call(call: dict[str, Any], user: dict[str, Any]) -> None:
    """Bosh menejer har qanday qo'ng'iroqni o'chira oladi (audio, transcript
    va analiz bilan birga). Menejer — faqat audio hali yuklanmagan
    (bekor qilingan) o'z qo'ng'iroq qoralamasini."""
    if not is_boss(user):
        if int(call.get("managerId") or 0) != _uid(user):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Bu qo'ng'iroqni o'chira olmaysiz.")
        if call.get("recording"):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Audio saqlangan qo'ng'iroq arxivdan faqat Bosh menejer tomonidan o'chiriladi.",
            )
    path = recording_path(call)
    if path is not None:
        with suppress(OSError):
            path.unlink()
    _delete_by_call("call_transcripts", int(call["id"]))
    _delete_by_call("call_analyses", int(call["id"]))
    storage.delete("calls", int(call["id"]))
    notify.log(user.get("name", ""), "delete", "call", call.get("leadName", ""),
               {"callId": call["id"]}, actor_id=_uid(user))


# ——————————————————————————————————————————————————————————
#  2. Recording (audio) saqlash
# ——————————————————————————————————————————————————————————


def detect_audio(head: bytes) -> tuple[str, str] | None:
    """Fayl turini kengaytma/Content-Type ga emas, imzoga (magic bytes) qarab aniqlaydi."""
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav", "audio/wav"
    if head[:4] == b"OggS":
        return "ogg", "audio/ogg"
    if head[:4] == b"fLaC":
        return "flac", "audio/flac"
    if head[:3] == b"ID3" or (len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0):
        return "mp3", "audio/mpeg"
    if len(head) >= 8 and head[4:8] == b"ftyp":
        return "m4a", "audio/mp4"
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "webm", "audio/webm"
    return None


def recording_path(call: dict[str, Any]) -> Path | None:
    rec = call.get("recording") or None
    if not rec or not rec.get("file"):
        return None
    root = config.RECORDINGS_DIR.resolve()
    path = (root / str(rec["file"])).resolve()
    # Path traversal himoyasi: fayl faqat RECORDINGS_DIR ichida bo'lishi shart
    if path.parent != root:
        return None
    return path


def _wav_duration(path: Path) -> float | None:
    try:
        with wave.open(str(path), "rb") as wf:
            frames, rate = wf.getnframes(), wf.getframerate()
            return round(frames / float(rate), 2) if rate else None
    except (wave.Error, EOFError, OSError):
        return None


async def save_recording(
    call: dict[str, Any], upload: UploadFile, client_duration: float, user: dict[str, Any]
) -> dict[str, Any]:
    if call.get("recording"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Bu qo'ng'iroq uchun audio allaqachon saqlangan. Yangi suhbat uchun yangi qo'ng'iroq yarating.",
        )

    max_bytes = config.CALL_MAX_UPLOAD_MB * 1024 * 1024
    token = uuid.uuid4().hex
    part = config.RECORDINGS_DIR / f"{token}.part"
    size = 0
    digest = hashlib.sha256()
    head = b""
    try:
        with part.open("wb") as fh:
            while True:
                chunk = await upload.read(CHUNK)
                if not chunk:
                    break
                if len(head) < 16:
                    head += chunk[: 16 - len(head)]
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(
                        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        f"Audio juda katta (maksimum {config.CALL_MAX_UPLOAD_MB} MB).",
                    )
                digest.update(chunk)
                fh.write(chunk)
            fh.flush()
            os.fsync(fh.fileno())

        if size < MIN_AUDIO_BYTES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Audio bo'sh yoki juda qisqa.")
        kind = detect_audio(head)
        if kind is None:
            raise HTTPException(
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                "Audio formati qo'llab-quvvatlanmaydi (WAV, MP3, OGG, FLAC, M4A, WEBM).",
            )
        ext, mime = kind
        final = config.RECORDINGS_DIR / f"{token}.{ext}"
        os.replace(part, final)
    except BaseException:
        with suppress(OSError):
            part.unlink()
        raise

    duration = _wav_duration(final) if ext == "wav" else None
    if duration is None:
        duration = max(0.0, min(float(client_duration or 0), MAX_DURATION_SECONDS))

    def _do(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        for row in rows:
            if int(row.get("id", 0)) == int(call["id"]):
                if row.get("recording"):  # parallel ikki yuklash bo'lsa — ikkinchisi rad etiladi
                    return None
                row["recording"] = {
                    "file": final.name,
                    "mime": mime,
                    "size": size,
                    "sha256": digest.hexdigest(),
                    "uploadedAt": storage.now_iso(),
                    "uploadedBy": _uid(user),
                }
                row["duration"] = duration
                row["status"] = "recorded"
                row["updatedAt"] = storage.now_iso()
                return dict(row)
        return None

    updated = storage.mutate("calls", _do)
    if updated is None:
        with suppress(OSError):
            final.unlink()
        raise HTTPException(status.HTTP_409_CONFLICT, "Audio allaqachon saqlangan yoki qo'ng'iroq o'chirilgan.")
    return updated


# ——————————————————————————————————————————————————————————
#  3. Transcript
# ——————————————————————————————————————————————————————————


def build_segments(stt: dict[str, Any]) -> list[dict[str, Any]]:
    """So'z darajasidagi diarization natijasini suhbat navbatlariga (segment) yig'adi."""
    words = [w for w in (stt.get("words") or []) if isinstance(w, dict) and str(w.get("text", "")).strip()]
    if not words:
        text = str(stt.get("text") or "").strip()
        return [{"speaker": 0, "start": 0.0, "end": float(stt.get("duration") or 0), "text": text}] if text else []

    segments: list[dict[str, Any]] = []
    for w in words:
        speaker = int(w.get("speaker") or 0)
        token = str(w.get("text", "")).strip()
        start = float(w.get("start") or 0)
        end = float(w.get("end") or start)
        if segments and segments[-1]["speaker"] == speaker:
            segments[-1]["text"] += " " + token
            segments[-1]["end"] = end
        else:
            segments.append({"speaker": speaker, "start": start, "end": end, "text": token})
    for s in segments:
        s["start"] = round(s["start"], 2)
        s["end"] = round(s["end"], 2)
    return segments


ROLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "agent_speaker": {"type": "integer", "description": "Turagentlik xodimi bo'lgan speaker raqami"},
    },
    "required": ["agent_speaker"],
    "additionalProperties": False,
}


async def assign_roles(segments: list[dict[str, Any]]) -> tuple[dict[int, str], str, str]:
    """Speaker raqamlarini ADMIN (xodim) / MIJOZ ga moslaydi.

    Qaytaradi: (roleMap, method, warning). method: single | ai | heuristic.
    Grok ishlamasa ham transcript yo'qolmaydi — heuristikaga tushiladi va
    foydalanuvchi rollarni bir tugma bilan almashtira oladi.
    """
    speakers = sorted({int(s["speaker"]) for s in segments})
    if not speakers:
        return {}, "single", ""
    if len(speakers) == 1:
        return (
            {speakers[0]: "ADMIN"},
            "single",
            "Yozuvda faqat bitta ovoz aniqlandi. Brauzer faqat kompyuter mikrofonini yozadi — "
            "mijoz ovozi yozilishi uchun telefonni karnay (speaker) rejimida mikrofonga yaqin qo'ying.",
        )

    agent: int | None = None
    method = "heuristic"
    if config.call_analysis_enabled():
        sample = "\n".join(f"S{s['speaker']}: {s['text']}" for s in segments[:40])[:8000]
        try:
            result = await _chat_json(
                "Sen turagentlik call-center yozuvlarini tahlil qilasan. Transcriptda speakerlar "
                "S0, S1, ... deb belgilangan. Qaysi speaker turagentlik XODIMI (sotuvchi/menejer) "
                "ekanini aniqla. Xodim odatda kompaniya nomini aytadi, tur/narx haqida "
                "tushuntiradi, savollar beradi. Faqat JSON qaytar.",
                sample,
                ROLE_SCHEMA,
                "speaker_roles",
                temperature=0,
            )
            candidate = int(result.get("agent_speaker", -1))
            if candidate in speakers:
                agent, method = candidate, "ai"
        except (*AiError, TypeError, ValueError):
            agent = None

    if agent is None:
        # Heuristika: sotuvchi odatda ko'proq gapiradi.
        counts: dict[int, int] = {}
        for s in segments:
            counts[int(s["speaker"])] = counts.get(int(s["speaker"]), 0) + len(str(s["text"]).split())
        agent = max(speakers, key=lambda sp: counts.get(sp, 0))

    role_map = {sp: ("ADMIN" if sp == agent else "MIJOZ") for sp in speakers}
    return role_map, method, ""


LABEL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "admin": {
            "type": "array",
            "items": {"type": "integer"},
            "description": "Turagentlik XODIMI aytgan segmentlar raqamlari",
        },
    },
    "required": ["admin"],
    "additionalProperties": False,
}

LABEL_SYSTEM = (
    "Sen turagentlik call-center yozuvlarini tahlil qilasan. Senga telefon suhbatining "
    "raqamlangan bo'laklari beriladi ([0], [1], ...). Ovoz egasi belgilanmagan. Har bir "
    "bo'lakni kim aytganini aniqla: turagentlik XODIMI (salomlashadi, kompaniya nomini aytadi, "
    "tur/narx/sanalarni tushuntiradi, savol beradi) yoki MIJOZ (o'z ehtiyojini, byudjetini, "
    "savollarini aytadi). Faqat XODIM aytgan bo'laklar raqamlarini 'admin' ro'yxatida qaytar."
)
LABEL_BATCH_CHARS = 6000
LABEL_SEGMENT_CHARS = 120
LABEL_MAX_BATCHES = 4


def _merge_turns(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ketma-ket bir xil speakerdagi segmentlarni bitta navbatga birlashtiradi."""
    out: list[dict[str, Any]] = []
    for seg in segments:
        if out and out[-1]["speaker"] == seg["speaker"]:
            out[-1]["text"] += " " + seg["text"]
            out[-1]["end"] = seg["end"]
        else:
            out.append(dict(seg))
    return out


async def label_whisper_segments(
    raw: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[int, str], str, str]:
    """Whisper diarization bermaydi — har bir segment AI bilan ADMIN/MIJOZ ga ajratiladi.

    Qaytaradi: (segments, roleMap, method, warning). speaker 0 = ADMIN, 1 = MIJOZ.
    AI ishlamasa transcript baribir saqlanadi (bitta ovoz sifatida) va
    foydalanuvchi rollarni keyin almashtira oladi.
    """
    batches: list[list[int]] = [[]]
    used = 0
    for i, seg in enumerate(raw):
        size = min(len(seg["text"]), LABEL_SEGMENT_CHARS) + 8
        if batches[-1] and used + size > LABEL_BATCH_CHARS:
            batches.append([])
            used = 0
        batches[-1].append(i)
        used += size

    labels: dict[int, int] = {}
    warning = ""
    try:
        for batch in batches[:LABEL_MAX_BATCHES]:
            text = "\n".join(f"[{i}] {raw[i]['text'][:LABEL_SEGMENT_CHARS]}" for i in batch)
            # Yordamchi vazifa yengilroq (zaxira) modelda bajariladi — asosiy model
            # token limiti qo'ng'iroq analizi uchun qoladi.
            result = await _chat_json(
                LABEL_SYSTEM, text, LABEL_SCHEMA, "segment_roles", temperature=0,
                model_order=[config.GROQ_FALLBACK_MODEL, config.GROQ_MODEL], max_tokens=1500,
            )
            admin = {int(x) for x in (result.get("admin") or []) if str(x).lstrip("-").isdigit()}
            for i in batch:
                labels[i] = 0 if i in admin else 1
    except (*AiError, TypeError, ValueError) as exc:
        logger.warning("Segment rollarini aniqlab bo'lmadi: %s", getattr(exc, "code", type(exc).__name__))
        labels = {}

    if not labels:
        segments = _merge_turns([{**s, "speaker": 0} for s in raw])
        return (
            segments,
            {0: "ADMIN"},
            "single",
            "Ovozlar (xodim/mijoz) avtomatik ajratilmadi — butun matn bitta ovoz sifatida saqlandi. "
            "Transcript va AI analiz baribir ishlaydi.",
        )

    last = 0
    marked = []
    for i, seg in enumerate(raw):
        if i in labels:
            last = labels[i]
        else:
            warning = "Qo'ng'iroq juda uzun — oxirgi qismlarda xodim/mijoz rollari taxminiy."
        marked.append({**seg, "speaker": labels.get(i, last)})
    segments = _merge_turns(marked)
    speakers = sorted({s["speaker"] for s in segments})
    role_map = {sp: ("ADMIN" if sp == 0 else "MIJOZ") for sp in speakers}
    return segments, role_map, "ai", warning


def _apply_roles(segments: list[dict[str, Any]], role_map: dict[int, str]) -> list[dict[str, Any]]:
    return [{**s, "role": role_map.get(int(s["speaker"]), "MIJOZ")} for s in segments]


def _set_call(call_id: int, patch: dict[str, Any]) -> dict[str, Any] | None:
    return storage.update("calls", int(call_id), patch)


def _begin(call: dict[str, Any], kind: str) -> None:
    """kind = transcript | analysis. Parallel ikki marta ishga tushishni bloklaydi."""
    proc = (call.get("processing") or {}).get(kind)
    if call.get(f"{kind}Status") == "processing" and _is_locked(proc):
        raise HTTPException(status.HTTP_409_CONFLICT, "Bu jarayon allaqachon bajarilmoqda. Biroz kuting.")
    processing = dict(call.get("processing") or {})
    processing[kind] = storage.now_iso()
    _set_call(int(call["id"]), {f"{kind}Status": "processing", f"{kind}Error": "", "processing": processing})


async def transcribe_call(call: dict[str, Any], user: dict[str, Any]) -> dict[str, Any]:
    path = recording_path(call)
    if path is None or not path.exists():
        raise HTTPException(status.HTTP_409_CONFLICT, "Avval qo'ng'iroq audiosi saqlanishi kerak.")
    _begin(call, "transcript")

    try:
        keyterms = ["SADAF"] + [t for t in [call.get("leadName", "")] if t]
        lead = storage.get_one("leads", int(call.get("leadId") or 0))
        if lead and lead.get("tour"):
            keyterms.append(str(lead["tour"]))
        mime = (call.get("recording") or {}).get("mime", "audio/wav")
        stt, provider, stt_model = await _stt(path, mime, keyterms)
        if provider == "groq":
            raw = stt.get("segments") or []
            if not raw and stt.get("text"):
                raw = [{"start": 0.0, "end": float(stt.get("duration") or 0), "text": stt["text"]}]
            if not raw:
                raise GroqError("Audioda nutq aniqlanmadi. Mikrofon ishlaganini tekshirib, qayta yozib ko'ring.",
                                "empty_transcript")
            segments, role_map, method, warning = await label_whisper_segments(raw)
        else:
            segments = build_segments(stt)
            if not segments:
                raise XaiError("Audioda nutq aniqlanmadi. Mikrofon ishlaganini tekshirib, qayta yozib ko'ring.",
                               "empty_transcript")
            role_map, method, warning = await assign_roles(segments)
    except AiError as exc:
        _set_call(int(call["id"]), {"transcriptStatus": "failed", "transcriptError": exc.message})
        raise _ai_http_error(exc) from exc
    except Exception as exc:  # noqa: BLE001 — audio yo'qolmaydi, status failed bo'ladi
        logger.exception("Transcript xatosi (call %s)", call.get("id"))
        message = "Transcript tayyorlashda kutilmagan xato. Qayta urinib ko'ring."
        _set_call(int(call["id"]), {"transcriptStatus": "failed", "transcriptError": message})
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, message) from exc

    segments = _apply_roles(segments, role_map)
    transcript = _upsert_by_call(
        "call_transcripts",
        int(call["id"]),
        {
            "leadId": int(call.get("leadId") or 0),
            "managerId": int(call.get("managerId") or 0),
            "provider": provider,
            "model": stt_model,
            "language": stt.get("language", ""),
            "duration": float(stt.get("duration") or 0),
            "text": str(stt.get("text") or "").strip(),
            "segments": segments,
            "roleMap": {str(k): v for k, v in role_map.items()},
            "roleMethod": method,
            "warning": warning,
        },
    )
    patch: dict[str, Any] = {
        "transcriptStatus": "done",
        "transcriptError": "",
        "status": "transcribed" if call.get("analysisStatus") != "done" else call.get("status"),
    }
    if call.get("analysisStatus") == "done":
        patch["analysisOutdated"] = True
    if not float(call.get("duration") or 0) and transcript["duration"]:
        patch["duration"] = transcript["duration"]
    _set_call(int(call["id"]), patch)
    return transcript


def swap_roles(call: dict[str, Any]) -> dict[str, Any]:
    transcript = get_by_call("call_transcripts", int(call["id"]))
    if not transcript:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Transcript hali tayyor emas.")
    flip = {"ADMIN": "MIJOZ", "MIJOZ": "ADMIN"}
    role_map = {k: flip.get(v, v) for k, v in (transcript.get("roleMap") or {}).items()}
    segments = [{**s, "role": flip.get(s.get("role", ""), s.get("role", ""))} for s in transcript.get("segments") or []]
    updated = _upsert_by_call(
        "call_transcripts", int(call["id"]),
        {**{k: v for k, v in transcript.items() if k not in ("id", "version", "createdAt", "updatedAt")},
         "roleMap": role_map, "segments": segments, "roleMethod": "manual"},
    )
    if call.get("analysisStatus") == "done":
        _set_call(int(call["id"]), {"analysisOutdated": True})
    return updated


# ——————————————————————————————————————————————————————————
#  4. Grok AI analiz
# ——————————————————————————————————————————————————————————

_CHECK_ITEM = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["pass", "fail", "na"]},
        "comment": {"type": "string"},
    },
    "required": ["status", "comment"],
    "additionalProperties": False,
}


def _checklist_schema(items: list[tuple[str, str]]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {key: _CHECK_ITEM for key, _ in items},
        "required": [key for key, _ in items],
        "additionalProperties": False,
    }


_RATING = {"type": "string", "enum": list(RATING_LABELS)}

ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "overall_rating": _RATING,
        "overall_score": {"type": "integer", "minimum": 0, "maximum": 100},
        "summary": {"type": "string"},
        "communication_rating": _RATING,
        "communication": _checklist_schema(COMMUNICATION_ITEMS),
        "sales_rating": _RATING,
        "sales": _checklist_schema(SALES_ITEMS),
        "lead": {
            "type": "object",
            "properties": {
                "interest": {"type": "string"},
                "main_need": {"type": "string"},
                "main_problem": {"type": "string"},
                "budget": {"type": "string"},
                "travel_date": {"type": "string"},
                "sales_opportunity": {"type": "string", "enum": list(OPPORTUNITY_LABELS)},
                "sales_opportunity_reason": {"type": "string"},
                "next_step": {"type": "string"},
            },
            "required": [
                "interest", "main_need", "main_problem", "budget", "travel_date",
                "sales_opportunity", "sales_opportunity_reason", "next_step",
            ],
            "additionalProperties": False,
        },
        "problems": {"type": "array", "items": {"type": "string"}, "maxItems": 15},
        "recommendations": {"type": "array", "items": {"type": "string"}, "maxItems": 15},
        "inappropriate_phrases": {"type": "array", "items": {"type": "string"}, "maxItems": 15},
        "follow_up": {
            "type": "object",
            "properties": {"agreed": {"type": "boolean"}, "when": {"type": "string"}},
            "required": ["agreed", "when"],
            "additionalProperties": False,
        },
    },
    "required": [
        "overall_rating", "overall_score", "summary", "communication_rating", "communication",
        "sales_rating", "sales", "lead", "problems", "recommendations",
        "inappropriate_phrases", "follow_up",
    ],
    "additionalProperties": False,
}

ANALYSIS_SYSTEM = (
    "Sen SADAF turagentligi call-centeri uchun sifat nazorati (QA) va sotuv bo'yicha "
    "ekspertsan. Senga xodim (ADMIN) va mijoz (MIJOZ) o'rtasidagi telefon suhbati "
    "transcripti beriladi.\n\n"
    "Qoidalar:\n"
    "1. FAQAT transcriptdagi faktlarga tayan. Transcriptda yo'q narsani o'ylab topma.\n"
    "2. Har bir checklist bandiga status ber: pass — bajarilgan, fail — bajarilishi kerak "
    "edi lekin bajarilmagan, na — suhbatda bunga ehtiyoj bo'lmagan (masalan mijoz e'tiroz "
    "bildirmagan bo'lsa objection_handling = na). comment — 1 qisqa gapda asos.\n"
    "3. no_rude_language: qo'pol yoki nomaqbul ibora bo'lsa fail va iboralarni "
    "inappropriate_phrases ga aynan yoz.\n"
    "4. problems — aniq, amaliy muammolar (masalan: 'Mijozning safar sanasi aniqlanmagan'). "
    "recommendations — har bir muammoga mos aniq harakat (masalan: 'Mijozdan safar sanasini so'rash').\n"
    "5. lead bo'limida ma'lumot transcriptda bo'lmasa 'Aniqlanmagan' deb yoz.\n"
    "6. Transcript avtomatik speech-to-text natijasi — kichik imlo xatolari bo'lishi mumkin, "
    "ularni xodim xatosi deb hisoblama.\n"
    "7. Barcha matnlar o'zbek tilida (lotin yozuvida), qisqa va aniq bo'lsin."
)


def _norm_rating(value: Any) -> dict[str, str]:
    key = str(value or "").lower()
    if key not in RATING_LABELS:
        key = "average"
    return {"key": key, "label": RATING_LABELS[key]}


def _norm_checklist(raw: Any, items: list[tuple[str, str]]) -> list[dict[str, str]]:
    raw = raw if isinstance(raw, dict) else {}
    out = []
    for key, label in items:
        entry = raw.get(key) if isinstance(raw.get(key), dict) else {}
        st = str(entry.get("status", "na")).lower()
        out.append({
            "key": key,
            "label": label,
            "status": st if st in ("pass", "fail", "na") else "na",
            "comment": str(entry.get("comment", "")).strip(),
        })
    return out


def _str_list(value: Any, limit: int = 15) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()][:limit]


def normalize_analysis(raw: dict[str, Any]) -> dict[str, Any]:
    """Model javobini UI uchun barqaror ko'rinishga keltiradi (sxemadan
    chetga chiqqan javob ham CRM ni buzmasin)."""
    lead: dict[str, Any] = raw["lead"] if isinstance(raw.get("lead"), dict) else {}
    opp = str(lead.get("sales_opportunity", "medium")).lower()
    opp = opp if opp in OPPORTUNITY_LABELS else "medium"
    follow: dict[str, Any] = raw["follow_up"] if isinstance(raw.get("follow_up"), dict) else {}
    try:
        score = max(0, min(100, int(raw.get("overall_score", 0))))
    except (TypeError, ValueError):
        score = 0
    return {
        "rating": _norm_rating(raw.get("overall_rating")),
        "score": score,
        "summary": str(raw.get("summary", "")).strip(),
        "communication": {
            "rating": _norm_rating(raw.get("communication_rating")),
            "items": _norm_checklist(raw.get("communication"), COMMUNICATION_ITEMS),
        },
        "sales": {
            "rating": _norm_rating(raw.get("sales_rating")),
            "items": _norm_checklist(raw.get("sales"), SALES_ITEMS),
        },
        "lead": {
            "interest": str(lead.get("interest", "")).strip(),
            "mainNeed": str(lead.get("main_need", "")).strip(),
            "mainProblem": str(lead.get("main_problem", "")).strip(),
            "budget": str(lead.get("budget", "")).strip(),
            "travelDate": str(lead.get("travel_date", "")).strip(),
            "salesOpportunity": {"key": opp, "label": OPPORTUNITY_LABELS[opp]},
            "salesOpportunityReason": str(lead.get("sales_opportunity_reason", "")).strip(),
            "nextStep": str(lead.get("next_step", "")).strip(),
        },
        "problems": _str_list(raw.get("problems")),
        "recommendations": _str_list(raw.get("recommendations")),
        "inappropriatePhrases": _str_list(raw.get("inappropriate_phrases")),
        "followUp": {"agreed": bool(follow.get("agreed", False)), "when": str(follow.get("when", "")).strip()},
    }


def _summary_of(result: dict[str, Any]) -> dict[str, Any]:
    items = result["communication"]["items"] + result["sales"]["items"]
    return {
        "rating": result["rating"]["key"],
        "ratingLabel": result["rating"]["label"],
        "score": result["score"],
        "problems": len(result["problems"]),
        "passed": sum(1 for i in items if i["status"] == "pass"),
        "failed": sum(1 for i in items if i["status"] == "fail"),
        "followUpAgreed": result["followUp"]["agreed"],
        "salesOpportunity": result["lead"]["salesOpportunity"]["key"],
    }


def _transcript_limit() -> int:
    """Groq Free Tier token limiti (~8K/daqiqa) uchun transcript qisqartiriladi."""
    if config.call_provider() == "groq":
        return max(2000, min(MAX_TRANSCRIPT_CHARS, config.GROQ_CALL_TRANSCRIPT_CHARS))
    return MAX_TRANSCRIPT_CHARS


def _transcript_text(transcript: dict[str, Any], limit: int = MAX_TRANSCRIPT_CHARS) -> str:
    lines = [f"{s.get('role', 'MIJOZ')}: {s.get('text', '')}" for s in transcript.get("segments") or []]
    text = "\n".join(lines) or str(transcript.get("text") or "")
    if len(text) <= limit:
        return text
    # Boshi va oxiri saqlanadi (salomlashish + kelishuv/keyingi qadam muhim).
    head = int(limit * 0.7)
    return text[:head] + "\n[... suhbatning o'rta qismi qisqartirildi ...]\n" + text[-(limit - head):]


async def analyze_call(call: dict[str, Any], user: dict[str, Any]) -> dict[str, Any]:
    transcript = get_by_call("call_transcripts", int(call["id"]))
    if not transcript or call.get("transcriptStatus") != "done":
        raise HTTPException(status.HTTP_409_CONFLICT, "Avval transcript tayyorlanishi kerak.")
    _begin(call, "analysis")

    lead = storage.get_one("leads", int(call.get("leadId") or 0)) or {}
    # Maxfiylik: modelga telefon raqami yuborilmaydi — faqat tahlil uchun kerakli kontekst.
    context = (
        f"Lead konteksti: qiziqqan tur — {lead.get('tour') or 'kiritilmagan'}; "
        f"bosqich — {lead.get('stage') or call.get('leadStage') or '—'}; "
        f"manba — {call.get('leadPlatform') or '—'}.\n"
        f"Qo'ng'iroq davomiyligi: {int(float(call.get('duration') or 0))} soniya.\n\n"
        "TRANSCRIPT:\n" + _transcript_text(transcript, _transcript_limit())
    )
    try:
        raw = await _chat_json(ANALYSIS_SYSTEM, context, ANALYSIS_SCHEMA, "call_analysis")
        result = normalize_analysis(raw)
    except AiError as exc:
        _set_call(int(call["id"]), {"analysisStatus": "failed", "analysisError": exc.message})
        raise _ai_http_error(exc) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("AI analiz xatosi (call %s)", call.get("id"))
        message = "AI analizda kutilmagan xato. Qayta urinib ko'ring."
        _set_call(int(call["id"]), {"analysisStatus": "failed", "analysisError": message})
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, message) from exc

    analysis = _upsert_by_call(
        "call_analyses",
        int(call["id"]),
        {
            "leadId": int(call.get("leadId") or 0),
            "managerId": int(call.get("managerId") or 0),
            "provider": config.call_provider(),
            "model": _analysis_model(),
            "transcriptVersion": int(transcript.get("version") or 1),
            "result": result,
        },
    )
    _set_call(int(call["id"]), {
        "analysisStatus": "done",
        "analysisError": "",
        "analysisOutdated": False,
        "analysisSummary": _summary_of(result),
        "status": "analyzed",
    })
    notify.log(user.get("name", ""), "analyze", "call", call.get("leadName", ""),
               {"callId": call["id"], "score": result["score"]}, actor_id=_uid(user))
    return analysis


# ——————————————————————————————————————————————————————————
#  5. Statistika (faqat real ma'lumotdan)
# ——————————————————————————————————————————————————————————


def _summarize(rows: list[dict[str, Any]], today: str) -> dict[str, Any]:
    analyzed = [c for c in rows if c.get("analysisStatus") == "done"]
    scores = [int((c.get("analysisSummary") or {}).get("score") or 0) for c in analyzed]
    return {
        "calls": len(rows),
        "todayCalls": sum(1 for c in rows if c.get("date") == today),
        "recorded": sum(1 for c in rows if c.get("recording")),
        "transcribed": sum(1 for c in rows if c.get("transcriptStatus") == "done"),
        "analyzed": len(analyzed),
        "pendingAnalysis": sum(1 for c in rows if c.get("recording") and c.get("analysisStatus") != "done"),
        "failed": sum(
            1 for c in rows if "failed" in (c.get("transcriptStatus"), c.get("analysisStatus"))
        ),
        "problemsFound": sum(int((c.get("analysisSummary") or {}).get("problems") or 0) for c in analyzed),
        "callsWithProblems": sum(
            1 for c in analyzed if int((c.get("analysisSummary") or {}).get("problems") or 0) > 0
        ),
        "followUps": sum(1 for c in analyzed if (c.get("analysisSummary") or {}).get("followUpAgreed")),
        "missedFollowUps": sum(
            1 for c in analyzed if not (c.get("analysisSummary") or {}).get("followUpAgreed")
        ),
        "avgScore": round(sum(scores) / len(scores), 1) if scores else None,
        "totalDuration": round(sum(float(c.get("duration") or 0) for c in rows), 1),
        "lastActivity": max((str(c.get("updatedAt") or c.get("createdAt") or "") for c in rows), default=None),
    }


def stats(user: dict[str, Any]) -> dict[str, Any]:
    rows = visible_calls(user)
    today = storage.today_uz()
    out: dict[str, Any] = {
        "scope": "all" if is_boss(user) else "own",
        "today": today,
        "totals": _summarize(rows, today),
        "config": {
            "stt": config.call_stt_enabled(),
            "analysis": config.call_analysis_enabled(),
            "provider": config.call_provider() or None,
        },
    }

    by_manager: dict[int, list[dict[str, Any]]] = {}
    for c in rows:
        by_manager.setdefault(int(c.get("managerId") or 0), []).append(c)

    if is_boss(user):
        users = {int(u["id"]): u for u in storage.read("users")}
        # Barcha faol menejerlar (0 ta qo'ng'iroq bo'lsa ham) + qo'ng'iroq qilgan har kim.
        ids = {uid for uid, u in users.items() if u.get("role") == "admin" and u.get("active", True)}
        ids |= set(by_manager)
    else:
        users = {_uid(user): user}
        ids = {_uid(user)}

    managers = []
    for mid in ids:
        u = users.get(mid, {})
        mine = by_manager.get(mid, [])
        name = u.get("name") or next((c.get("managerName") for c in mine if c.get("managerName")), "—")
        managers.append({
            "managerId": mid,
            "name": name,
            "role": u.get("role", ""),
            "roleLabel": ROLE_LABELS.get(u.get("role", ""), ""),
            **_summarize(mine, today),
        })
    managers.sort(key=lambda m: (-m["calls"], str(m["name"])))
    out["managers"] = managers
    return out
