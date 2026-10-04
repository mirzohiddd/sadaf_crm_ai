"""Groq (OpenAI-mos Chat Completions API) bilan ishlash uchun yagona servis.

- ``POST {GROQ_BASE_URL}/chat/completions`` — CRM AI yordamchi (tool calling)
  va AI Call Center analizi (``chat_json`` — JSON-sxema).
- ``POST {GROQ_BASE_URL}/audio/transcriptions`` — AI Call Center
  speech-to-text (Whisper, ``transcribe``).

Ishonchlilik:
- Asosiy model (``GROQ_MODEL``) limitga tushsa (429), vaqtincha ishlamasa
  (5xx / timeout) yoki o'chirilgan bo'lsa — ``GROQ_FALLBACK_MODEL`` sinab
  ko'riladi (Groq'da har bir modelning limiti alohida hisoblanadi).
- Model noto'g'ri tool chaqiruvi yaratsa (``tool_use_failed``) — bir marta
  qayta so'raladi.
- Reasoning parametrlari (``reasoning_effort``/``include_reasoning``) faqat
  GPT-OSS modellariga yuboriladi; model ularni qabul qilmasa — ularsiz
  qayta yuboriladi.

XAVFSIZLIK:
- ``GROQ_API_KEY`` faqat shu modulda, faqat ``Authorization`` headerida
  ishlatiladi. Hech qachon log qilinmaydi, xato xabariga qo'shilmaydi,
  frontendga yoki bazaga yozilmaydi.
- Tashqi API qaytargan xom xato matni foydalanuvchiga ko'rsatilmaydi.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx

from .. import config

logger = logging.getLogger("sadaf.groq")


class GroqError(Exception):
    """Foydalanuvchiga ko'rsatsa bo'ladigan xato (kalit/ichki ma'lumotsiz)."""

    def __init__(
        self, message: str, code: str = "groq_error", status: int | None = None, retryable: bool = False
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status
        self.retryable = retryable


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {config.GROQ_API_KEY}",
        "Content-Type": "application/json",
    }


def models() -> list[str]:
    """Sinab ko'riladigan modellar tartibi (takrorlanmasdan)."""
    out: list[str] = []
    for m in (config.GROQ_MODEL, config.GROQ_FALLBACK_MODEL):
        m = (m or "").strip()
        if m and m not in out:
            out.append(m)
    return out


def _is_reasoning_model(model: str) -> bool:
    return model.startswith("openai/gpt-oss")


def build_payload(
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    *,
    tool_choice: str = "auto",
    reasoning: bool = True,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": config.AI_TEMPERATURE,
        "max_completion_tokens": config.AI_MAX_TOKENS,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = tool_choice
    if reasoning and _is_reasoning_model(model):
        if config.GROQ_REASONING_EFFORT in ("low", "medium", "high"):
            payload["reasoning_effort"] = config.GROQ_REASONING_EFFORT
        # Fikrlash matni javobga qo'shilmasin (trafik va xavfsizlik uchun).
        payload["include_reasoning"] = False
    return payload


def _error_info(resp: httpx.Response) -> tuple[str, str]:
    """(error.code, error.message) — Groq xato tanasidan."""
    try:
        err = resp.json().get("error") or {}
        return str(err.get("code") or err.get("type") or ""), str(err.get("message") or "")
    except Exception:  # noqa: BLE001
        return "", ""


def _to_error(resp: httpx.Response) -> GroqError:
    status = resp.status_code
    code, message = _error_info(resp)
    # Xom javob faqat server logiga, qisqa ko'rinishda (kalit unda bo'lmaydi).
    logger.warning("Groq xatosi: HTTP %s code=%s %s", status, code, message[:300])
    low = message.lower()

    if status in (401, 403):
        return GroqError(
            "Groq API kaliti noto'g'ri yoki ruxsat yo'q. Administrator .env dagi GROQ_API_KEY ni tekshirsin.",
            "groq_auth", status,
        )
    if status == 429:
        return GroqError(
            "Groq so'rovlar limiti vaqtincha tugadi. Birozdan keyin qayta urinib ko'ring.",
            "groq_rate_limit", status, retryable=True,
        )
    if code == "json_validate_failed":
        return GroqError("Model sxemaga mos JSON qaytarmadi.", "groq_json_invalid", status)
    if code == "tool_use_failed":
        return GroqError("Model funksiyani noto'g'ri chaqirdi.", "groq_tool_use_failed", status, retryable=True)
    if status == 404 or code in ("model_not_found", "model_decommissioned") or "decommissioned" in low:
        return GroqError(
            "Groq modeli topilmadi yoki o'chirilgan. .env dagi GROQ_MODEL qiymatini tekshiring.",
            "groq_model_unavailable", status, retryable=True,
        )
    if status == 400 and ("reasoning" in low or "include_reasoning" in low):
        return GroqError("Model reasoning parametrlarini qabul qilmadi.", "groq_reasoning_unsupported", status)
    if status == 413 or code == "context_length_exceeded" or ("context" in low and "length" in low):
        return GroqError("So'rov juda katta. Savolni qisqartiring.", "groq_too_large", status)
    if status == 400:
        return GroqError("Groq so'rovni qabul qilmadi (format yoki parametr xatosi).", "groq_bad_request", status)
    return GroqError(
        f"Groq xizmati vaqtincha ishlamayapti (HTTP {status}). Keyinroq qayta urinib ko'ring.",
        "groq_unavailable", status, retryable=True,
    )


async def _post(client: httpx.AsyncClient, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        resp = await client.post(
            f"{config.GROQ_BASE_URL}/chat/completions", headers=_headers(), json=payload
        )
    except httpx.TimeoutException as exc:
        raise GroqError("Groq javob berishga ulgurmadi (timeout).", "groq_timeout", retryable=True) from exc
    except httpx.HTTPError as exc:
        logger.warning("Groq tarmoq xatosi: %s", type(exc).__name__)
        raise GroqError("Groq serveriga ulanib bo'lmadi.", "groq_network", retryable=True) from exc

    if not resp.is_success:
        raise _to_error(resp)
    try:
        data = resp.json()
        message = data["choices"][0]["message"]
    except Exception as exc:  # noqa: BLE001
        raise GroqError("Groq kutilmagan formatda javob qaytardi.", "groq_bad_response", retryable=True) from exc
    if not isinstance(message, dict):
        raise GroqError("Groq kutilmagan formatda javob qaytardi.", "groq_bad_response", retryable=True)
    return message


async def _try_model(
    client: httpx.AsyncClient,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    tool_choice: str,
) -> dict[str, Any]:
    reasoning = True
    tool_retry_left = 1
    while True:
        payload = build_payload(model, messages, tools, tool_choice=tool_choice, reasoning=reasoning)
        try:
            return await _post(client, payload)
        except GroqError as err:
            if err.code == "groq_reasoning_unsupported" and reasoning:
                reasoning = False
                continue
            if err.code == "groq_tool_use_failed" and tool_retry_left > 0:
                tool_retry_left -= 1
                continue
            raise


async def complete(
    client: httpx.AsyncClient,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str = "auto",
) -> tuple[dict[str, Any], str]:
    """Bitta chat-completion. ``(assistant_message, ishlatilgan_model)`` qaytaradi.

    Qayta urinish mumkin bo'lgan xatoda keyingi (zaxira) modelga o'tadi.
    """
    if not config.GROQ_API_KEY:
        raise GroqError("GROQ_API_KEY sozlanmagan.", "groq_disabled")

    last: GroqError | None = None
    for model in models():
        try:
            return await _try_model(client, model, messages, tools, tool_choice), model
        except GroqError as err:
            last = err
            if not err.retryable:
                raise
            logger.info("Groq: %s modeli ishlamadi (%s), keyingisi sinab ko'riladi", model, err.code)
    assert last is not None
    raise last


def new_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(config.GROQ_TIMEOUT_SECONDS, connect=10.0))


# ——————————————————————————————————————————————————————————
#  AI Call Center: speech-to-text (Groq Whisper)
# ——————————————————————————————————————————————————————————


def _not_configured(what: str) -> GroqError:
    return GroqError(
        f"{what} sozlanmagan: backend .env faylga GROQ_API_KEY qo'shing.", "groq_not_configured"
    )


async def transcribe(audio_path: Path, filename: str, mime: str, *, prompt: str = "") -> dict[str, Any]:
    """Audio → matn (``whisper-large-v3-turbo``, ``verbose_json``).

    Qaytaradi: ``{"text", "language", "duration", "segments": [{start, end, text}]}``.
    Whisper speaker diarization bermaydi — segmentlarda ``speaker`` yo'q.
    """
    if not config.GROQ_API_KEY:
        raise _not_configured("Speech-to-text")

    try:
        size = audio_path.stat().st_size
    except OSError as exc:
        raise GroqError("Audio faylni o'qib bo'lmadi.", "audio_read_error") from exc
    limit_mb = config.GROQ_STT_MAX_MB
    if size > limit_mb * 1024 * 1024:
        raise GroqError(
            f"Audio fayl hajmi {size / 1024 / 1024:.1f} MB — Groq speech-to-text faqat {limit_mb} MB gacha "
            f"qabul qiladi. Qisqaroq yozuv yuklang (brauzer yozuvi taxminan {limit_mb * 60 // 115} daqiqagacha "
            "sig'adi) yoki audioni siqilgan formatda (MP3/OGG) yuklang. Audio arxivda saqlanib qoldi.",
            "groq_too_large", 413,
        )

    data: dict[str, str] = {
        "model": config.GROQ_STT_MODEL,
        "response_format": "verbose_json",
        "temperature": "0",
    }
    if config.GROQ_STT_LANGUAGE:
        data["language"] = config.GROQ_STT_LANGUAGE
    if prompt:
        data["prompt"] = prompt[:400]  # Whisper prompt ~224 token bilan cheklangan

    try:
        audio_bytes = audio_path.read_bytes()
    except OSError as exc:
        raise GroqError("Audio faylni o'qib bo'lmadi.", "audio_read_error") from exc

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(config.GROQ_STT_TIMEOUT_SECONDS, connect=10.0)
        ) as client:
            resp = await client.post(
                f"{config.GROQ_BASE_URL}/audio/transcriptions",
                headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
                data=data,
                files={"file": (filename, audio_bytes, mime)},
            )
    except httpx.TimeoutException as exc:
        raise GroqError("Groq speech-to-text javob bermadi (timeout). Qayta urinib ko'ring.", "groq_timeout") from exc
    except httpx.HTTPError as exc:
        logger.warning("Groq STT tarmoq xatosi: %s", type(exc).__name__)
        raise GroqError("Groq serveriga ulanib bo'lmadi. Internet/tarmoqni tekshiring.", "groq_network") from exc

    if not resp.is_success:
        err = _to_error(resp)
        if err.code == "groq_too_large":
            err.message = (
                f"Audio Groq speech-to-text uchun juda katta (maksimum {limit_mb} MB). "
                "Qisqaroq yozuv yuklang. Audio arxivda saqlanib qoldi."
            )
        elif err.code == "groq_model_unavailable":
            err.message = "Groq STT modeli topilmadi. .env dagi GROQ_STT_MODEL qiymatini tekshiring."
        elif err.code == "groq_bad_request":
            err.message = (
                "Groq speech-to-text audioni qabul qilmadi (format yoki GROQ_STT_LANGUAGE noto'g'ri). "
                "WAV, MP3, OGG, FLAC, M4A yoki WEBM yuklang."
            )
        raise err

    try:
        payload = resp.json()
    except ValueError as exc:
        raise GroqError("Groq speech-to-text noto'g'ri javob qaytardi.", "groq_bad_response") from exc
    if not isinstance(payload, dict):
        raise GroqError("Groq speech-to-text noto'g'ri javob qaytardi.", "groq_bad_response")

    segments = []
    for seg in payload.get("segments") or []:
        if not isinstance(seg, dict):
            continue
        text = str(seg.get("text") or "").strip()
        # Jimlik ustida Whisper "xayoliy" matn chiqarishi mumkin — tashlab yuboramiz.
        if not text or float(seg.get("no_speech_prob") or 0) >= 0.9:
            continue
        start = float(seg.get("start") or 0)
        segments.append({"start": round(start, 2), "end": round(float(seg.get("end") or start), 2), "text": text})

    return {
        "text": str(payload.get("text") or "").strip(),
        "language": str(payload.get("language") or config.GROQ_STT_LANGUAGE or ""),
        "duration": float(payload.get("duration") or 0),
        "segments": segments,
    }


# ——————————————————————————————————————————————————————————
#  AI Call Center: JSON-sxemali analiz (GPT-OSS)
# ——————————————————————————————————————————————————————————

# Navbat bilan sinaladigan javob formatlari: avval qat'iy sxema, keyin
# yumshoq sxema, oxirida oddiy JSON rejimi (sxema promptda).
_JSON_MODES = ("strict", "schema", "object")


def _parse_json(content: Any) -> dict[str, Any] | None:
    if isinstance(content, list):
        content = "".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    text = str(content or "").strip()
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except ValueError:
            return None
    return data if isinstance(data, dict) else None


async def chat_json(
    system: str,
    user: str,
    schema: dict[str, Any],
    schema_name: str,
    *,
    temperature: float = 0.2,
    model_order: list[str] | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """Modeldan JSON-sxemaga mos javob oladi (standart: GROQ_MODEL → zaxira model)."""
    if not config.GROQ_API_KEY:
        raise _not_configured("AI analiz")

    order = [m for m in (model_order or models()) if m]
    order = list(dict.fromkeys(order))
    last: GroqError | None = None

    async with new_client() as client:
        for model in order:
            for mode in _JSON_MODES:
                sys_text = system
                if mode == "object":
                    sys_text += (
                        "\n\nJavobni FAQAT bitta JSON obyekt sifatida qaytar (izohsiz, markdownsiz). "
                        "JSON quyidagi sxemaga mos bo'lsin:\n" + json.dumps(schema, ensure_ascii=False)
                    )
                payload = build_payload(
                    model,
                    [{"role": "system", "content": sys_text}, {"role": "user", "content": user}],
                    None,
                )
                payload["temperature"] = temperature
                payload["max_completion_tokens"] = max_tokens or config.GROQ_CALL_MAX_TOKENS
                if mode == "object":
                    payload["response_format"] = {"type": "json_object"}
                else:
                    payload["response_format"] = {
                        "type": "json_schema",
                        "json_schema": {"name": schema_name, "schema": schema, "strict": mode == "strict"},
                    }
                try:
                    try:
                        message = await _post(client, payload)
                    except GroqError as err:
                        if err.code != "groq_reasoning_unsupported":
                            raise
                        payload.pop("reasoning_effort", None)
                        payload.pop("include_reasoning", None)
                        message = await _post(client, payload)
                except GroqError as err:
                    last = err
                    if err.code in ("groq_bad_request", "groq_json_invalid", "groq_bad_response"):
                        continue  # keyingi formatni sinaymiz
                    if err.retryable:
                        logger.info("Groq JSON: %s ishlamadi (%s), keyingi model", model, err.code)
                        break  # keyingi modelga o'tamiz
                    raise
                result = _parse_json(message.get("content"))
                if result is not None:
                    return result
                last = GroqError("AI javobini o'qib bo'lmadi (JSON formati buzilgan).", "groq_bad_response")

    if last is not None and last.code == "groq_too_large":
        last.message = (
            "Transcript Groq Free Tier token limiti uchun juda uzun. Qisqaroq qo'ng'iroqni tahlil "
            "qiling yoki .env dagi GROQ_CALL_TRANSCRIPT_CHARS ni kamaytiring."
        )
    raise last or GroqError("AI analiz bajarilmadi.", "groq_unavailable")
