"""xai_service so'rovlari to'g'ri tuzilishini va kalit hech qayerga
oqib chiqmasligini httpx.MockTransport orqali tekshiradi (internetsiz)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from app import config
from app.services import xai_service
from app.services.xai_service import XaiError

from .conftest import make_wav

KEY = "test-xai-key-SECRET"


def _patch_client(monkeypatch, handler):
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        return real(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(xai_service.httpx, "AsyncClient", factory)


def test_transcribe_request_shape(monkeypatch, tmp_path: Path):
    audio = tmp_path / "a.wav"
    audio.write_bytes(make_wav(0.5))
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        body = request.content
        seen["multipart"] = request.headers["content-type"].startswith("multipart/form-data")
        seen["diarize_before_file"] = body.index(b'name="diarize"') < body.index(b'name="file"')
        seen["keyterms"] = body.count(b'name="keyterm"')
        return httpx.Response(200, json={"text": "salom", "duration": 0.5, "words": []})

    _patch_client(monkeypatch, handler)
    result = asyncio.run(xai_service.transcribe(audio, "a.wav", "audio/wav", keyterms=["SADAF", "Turkiya"]))
    assert result["text"] == "salom"
    assert seen["url"] == f"{config.XAI_BASE_URL}/stt"
    assert seen["auth"] == f"Bearer {KEY}"
    assert seen["multipart"] is True
    assert seen["diarize_before_file"] is True
    assert seen["keyterms"] == 2


def test_chat_json_uses_env_model_and_schema(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}]})

    _patch_client(monkeypatch, handler)
    out = asyncio.run(xai_service.chat_json("sys", "user", {"type": "object"}, "x"))
    assert out == {"ok": True}
    assert seen["model"] == "test-grok-model"  # .env dan, hardcode emas
    assert seen["response_format"]["type"] == "json_schema"


@pytest.mark.parametrize("code", [401, 429, 500, 503])
def test_errors_are_friendly_and_never_leak_key(monkeypatch, code):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(code, text=f"bad key {KEY}")

    _patch_client(monkeypatch, handler)
    with pytest.raises(XaiError) as err:
        asyncio.run(xai_service.chat_json("s", "u", {"type": "object"}, "x"))
    assert KEY not in err.value.message
    assert err.value.message


def test_network_error_is_friendly(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    _patch_client(monkeypatch, handler)
    with pytest.raises(XaiError) as err:
        asyncio.run(xai_service.chat_json("s", "u", {"type": "object"}, "x"))
    assert "ulanib bo'lmadi" in err.value.message


def test_missing_model_disables_analysis(monkeypatch):
    monkeypatch.setattr(config, "XAI_MODEL", "")
    with pytest.raises(XaiError) as err:
        asyncio.run(xai_service.chat_json("s", "u", {"type": "object"}, "x"))
    assert err.value.code == "xai_not_configured"
    assert "XAI_MODEL" in err.value.message


def test_bad_json_from_model(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})

    _patch_client(monkeypatch, handler)
    with pytest.raises(XaiError) as err:
        asyncio.run(xai_service.chat_json("s", "u", {"type": "object"}, "x"))
    assert err.value.code == "xai_bad_response"
