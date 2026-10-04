"""xAI (Grok) bilan ishlash uchun yagona servis.

Ikki imkoniyat:

1. ``transcribe()`` — ``POST {XAI_BASE_URL}/stt`` (speech-to-text,
   speaker diarization bilan). Audio fayl multipart orqali yuboriladi.
2. ``chat_json()`` — ``POST {XAI_BASE_URL}/chat/completions`` ga
   ``response_format = json_schema`` bilan so'rov. Javob sxemaga mos JSON.

XAVFSIZLIK:
- ``XAI_API_KEY`` faqat shu modulda, faqat ``Authorization`` headerida
  ishlatiladi. U hech qachon log qilinmaydi, xato xabariga qo'shilmaydi,
  frontendga yoki bazaga yozilmaydi.
- Tashqi API qaytargan xom xato matni foydalanuvchiga ko'rsatilmaydi —
  o'rniga tushunarli o'zbekcha xabar (``XaiError.message``) qaytariladi.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx

from .. import config

logger = logging.getLogger("sadaf.xai")


class XaiError(Exception):
    """Foydalanuvchiga ko'rsatsa bo'ladigan xato (kalit/ichki ma'lumotsiz)."""

    def __init__(self, message: str, code: str = "xai_error", status: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {config.XAI_API_KEY}"}


def _raise_for_status(resp: httpx.Response, what: str) -> None:
    """HTTP xatoni tushunarli XaiError ga aylantiradi. Javob tanasi
    (unda kalit bo'lmasa ham) foydalanuvchiga uzatilmaydi — faqat
    server logiga qisqa ko'rinishda yoziladi."""
    if resp.is_success:
        return
    code = resp.status_code
    logger.warning("xAI %s xatosi: HTTP %s %s", what, code, resp.text[:300])
    if code in (401, 403):
        raise XaiError(
            "xAI API kaliti noto'g'ri yoki ruxsat yo'q. Administrator .env dagi XAI_API_KEY ni tekshirsin.",
            "xai_auth", code,
        )
    if code == 404:
        raise XaiError(
            "xAI model yoki endpoint topilmadi. .env dagi XAI_MODEL / XAI_STT_MODEL qiymatini tekshiring.",
            "xai_not_found", code,
        )
    if code == 413:
        raise XaiError("Audio fayl xAI uchun juda katta.", "xai_too_large", code)
    if code == 429:
        raise XaiError(
            "xAI so'rovlar limiti tugadi. Birozdan keyin qayta urinib ko'ring.", "xai_rate_limit", code
        )
    if code == 400:
        raise XaiError(
            f"xAI {what} so'rovini qabul qilmadi (format yoki parametr xatosi).", "xai_bad_request", code
        )
    raise XaiError(
        f"xAI xizmati vaqtincha ishlamayapti (HTTP {code}). Keyinroq qayta urinib ko'ring.",
        "xai_unavailable", code,
    )


async def transcribe(
    audio_path: Path,
    filename: str,
    mime: str,
    *,
    keyterms: list[str] | None = None,
) -> dict[str, Any]:
    """Audio faylni matnga aylantiradi (speaker diarization yoqilgan).

    Qaytaradi: xAI javobi ``{"text", "language", "duration", "words": [...]}``.
    Har bir ``words[i]`` da ``speaker`` (int) bo'ladi.
    """
    if not config.xai_stt_enabled():
        raise XaiError(
            "Speech-to-text sozlanmagan: .env faylga XAI_API_KEY qo'shing.", "xai_not_configured"
        )

    # Maydonlar tartibi muhim: xAI hujjatiga ko'ra `file` eng oxirida bo'lishi
    # kerak — httpx avval `data` maydonlarini, keyin `files` ni yozadi.
    # `data` albatta dict bo'lsin (ro'yxat berilsa httpx uni multipart emas,
    # xom content sifatida yuborib yuboradi).
    data: dict[str, str | list[str]] = {"diarize": "true"}
    if config.XAI_STT_MODEL:
        data["model"] = config.XAI_STT_MODEL
    if config.XAI_STT_LANGUAGE:
        data["language"] = config.XAI_STT_LANGUAGE
        data["format"] = "true"
    terms = [str(t).strip()[:50] for t in (keyterms or [])[:20] if str(t).strip()]
    if terms:
        data["keyterm"] = terms  # takrorlanuvchi maydon: keyterm=..&keyterm=..

    try:
        # httpx.AsyncClient multipart uchun sinxron fayl obyektini qabul
        # qilmaydi — shuning uchun bytes sifatida beriladi (hajm
        # CALL_MAX_UPLOAD_MB bilan cheklangan).
        audio_bytes = audio_path.read_bytes()
    except OSError as exc:
        raise XaiError("Audio faylni o'qib bo'lmadi.", "audio_read_error") from exc

    try:
        async with httpx.AsyncClient(timeout=config.XAI_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                f"{config.XAI_BASE_URL}/stt",
                headers=_headers(),
                data=data,
                files={"file": (filename, audio_bytes, mime)},
            )
    except httpx.TimeoutException as exc:
        raise XaiError("xAI speech-to-text javob bermadi (timeout). Qayta urinib ko'ring.", "xai_timeout") from exc
    except httpx.HTTPError as exc:
        raise XaiError("xAI serveriga ulanib bo'lmadi. Internet/tarmoqni tekshiring.", "xai_network") from exc

    _raise_for_status(resp, "speech-to-text")
    try:
        payload = resp.json()
    except ValueError as exc:
        raise XaiError("xAI speech-to-text noto'g'ri javob qaytardi.", "xai_bad_response") from exc
    if not isinstance(payload, dict):
        raise XaiError("xAI speech-to-text noto'g'ri javob qaytardi.", "xai_bad_response")
    return payload


async def chat_json(
    system: str,
    user: str,
    schema: dict[str, Any],
    schema_name: str,
    *,
    temperature: float = 0.2,
) -> dict[str, Any]:
    """Grok modelidan JSON-sxemaga qat'iy mos javob oladi."""
    if not config.xai_analysis_enabled():
        raise XaiError(
            "Grok AI analiz sozlanmagan: .env faylga XAI_API_KEY va XAI_MODEL qo'shing.",
            "xai_not_configured",
        )

    body = {
        "model": config.XAI_MODEL,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "schema": schema, "strict": True},
        },
    }
    try:
        async with httpx.AsyncClient(timeout=config.XAI_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                f"{config.XAI_BASE_URL}/chat/completions",
                headers={**_headers(), "Content-Type": "application/json"},
                json=body,
            )
    except httpx.TimeoutException as exc:
        raise XaiError("Grok AI javob bermadi (timeout). Qayta urinib ko'ring.", "xai_timeout") from exc
    except httpx.HTTPError as exc:
        raise XaiError("xAI serveriga ulanib bo'lmadi. Internet/tarmoqni tekshiring.", "xai_network") from exc

    _raise_for_status(resp, "AI analiz")
    try:
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        if isinstance(content, list):  # ba'zi javoblar bloklar ro'yxati bo'lishi mumkin
            content = "".join(
                str(part.get("text", "")) for part in content if isinstance(part, dict)
            )
        result = json.loads(str(content).strip().removeprefix("```json").removesuffix("```").strip())
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise XaiError("Grok AI javobini o'qib bo'lmadi (JSON formati buzilgan).", "xai_bad_response") from exc
    if not isinstance(result, dict):
        raise XaiError("Grok AI javobi kutilgan formatda emas.", "xai_bad_response")
    return result
