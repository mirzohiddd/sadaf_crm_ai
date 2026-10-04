"""AI Call Center — Groq oqimi testlari:
audio → Groq Whisper (STT) → transcript → GPT-OSS 120B (analiz).

Groq HTTP darajasida httpx.MockTransport bilan mock qilinadi — haqiqiy
groq_service kodi va so'rov shakli tekshiriladi (internet/kalit kerak emas).
"""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app import config, storage
from app.services import groq_service

from .conftest import login, make_wav
from .test_calls import ANALYSIS_RESULT

KEY = "gsk_call-center-test-SECRET"

WHISPER = {
    "text": "Assalomu alaykum, SADAF turagentligi. Turkiyaga tur kerak edi. Narxi 900 dollar.",
    "language": "uzbek",
    "duration": 6.2,
    "segments": [
        {"id": 0, "start": 0.0, "end": 2.0, "text": " Assalomu alaykum, SADAF turagentligi.", "no_speech_prob": 0.01},
        {"id": 1, "start": 2.1, "end": 3.9, "text": " Turkiyaga tur kerak edi.", "no_speech_prob": 0.02},
        {"id": 2, "start": 4.0, "end": 6.2, "text": " Narxi 900 dollar.", "no_speech_prob": 0.01},
        {"id": 3, "start": 6.2, "end": 9.0, "text": " Rahmat.", "no_speech_prob": 0.97},  # jimlik "xayoli"
    ],
}


class FakeGroq:
    """Groq API o'rnida: so'rovlarni yozib boradi, sozlanadigan javoblar beradi."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.stt_status = 200
        self.label_fail = False
        self.reject_strict = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {KEY}"
        path = request.url.path
        if path.endswith("/audio/transcriptions"):
            body = request.content
            self.requests.append({"kind": "stt", "url": str(request.url), "body": body})
            if self.stt_status == 429:
                return httpx.Response(429, json={"error": {"message": "Rate limit reached", "code": "rate_limit_exceeded"}})
            return httpx.Response(200, json=WHISPER)

        assert path.endswith("/chat/completions"), path
        body = json.loads(request.content)
        fmt = body.get("response_format") or {}
        name = (fmt.get("json_schema") or {}).get("name", "json_object")
        self.requests.append({"kind": "chat", "name": name, "model": body["model"], "format": fmt, "body": body})
        if self.reject_strict and (fmt.get("json_schema") or {}).get("strict"):
            return httpx.Response(400, json={"error": {"message": "invalid JSON schema for response_format"}})
        if name == "segment_roles":
            if self.label_fail:
                return httpx.Response(503, json={"error": {"message": "over capacity"}})
            content = {"admin": [0, 2]}
        else:
            assert "+998" not in body["messages"][1]["content"]  # telefon raqami modelga yuborilmaydi
            content = ANALYSIS_RESULT
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": json.dumps(content)}}]})


@pytest.fixture()
def groq(monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", KEY)
    monkeypatch.setattr(config, "GROQ_MODEL", "openai/gpt-oss-120b")
    monkeypatch.setattr(config, "GROQ_FALLBACK_MODEL", "openai/gpt-oss-20b")
    monkeypatch.setattr(config, "GROQ_STT_MODEL", "whisper-large-v3-turbo")
    monkeypatch.setattr(config, "GROQ_STT_LANGUAGE", "uz")
    monkeypatch.setattr(config, "GROQ_STT_MAX_MB", 25)
    # xAI umuman sozlanmagan — Call Center undan qat'i nazar ishlashi kerak
    monkeypatch.setattr(config, "XAI_API_KEY", "")
    monkeypatch.setattr(config, "XAI_MODEL", "")
    fake = FakeGroq()
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        return real(*args, transport=httpx.MockTransport(fake), **kwargs)

    monkeypatch.setattr(groq_service.httpx, "AsyncClient", factory)
    return fake


@pytest.fixture(scope="module")
def env(client):
    boss = login(client, "boss", "boss12345")
    resp = client.post("/api/employees", headers=boss, json={
        "name": "Groq Call Menejer", "crmLogin": "groqcall", "crmPassword": "secret12", "crmRole": "admin"})
    assert resp.status_code == 201, resp.text
    emp_id = resp.json()["id"]
    mgr = login(client, "groqcall", "secret12")
    lead = client.post("/api/leads", headers=boss, json={
        "name": "Groq Call Lead", "phone": "+998 90 555 44 33", "manager": "Groq Call Menejer", "tour": "Turkiya"})
    assert lead.status_code == 201, lead.text
    data = {"boss": boss, "mgr": mgr, "lead": lead.json(), "calls": []}
    yield data
    # Umumiy test bazasini boshqa modullar uchun tozalab qo'yamiz
    for cid in data["calls"]:
        client.delete(f"/api/calls/{cid}", headers=boss)
    client.delete(f"/api/leads/{data['lead']['id']}", headers=boss)
    client.delete(f"/api/employees/{emp_id}", headers=boss)


def _recorded_call(client, env) -> int:
    call = client.post("/api/calls", headers=env["mgr"], json={"leadId": env["lead"]["id"], "consent": True})
    assert call.status_code == 201, call.text
    cid = call.json()["id"]
    env["calls"].append(cid)
    up = client.post(f"/api/calls/{cid}/recording", headers=env["mgr"],
                     files={"file": ("call.wav", make_wav(1.0), "audio/wav")}, data={"duration": "1"})
    assert up.status_code == 200, up.text
    return cid


def test_full_pipeline_whisper_and_gpt_oss(client, env, groq):
    cid = _recorded_call(client, env)

    tr = client.post(f"/api/calls/{cid}/transcribe", headers=env["mgr"])
    assert tr.status_code == 200, tr.text
    transcript = tr.json()["transcript"]
    assert transcript["provider"] == "groq"
    assert transcript["model"] == "whisper-large-v3-turbo"
    assert transcript["leadId"] == env["lead"]["id"]
    # Whisper segmentlari AI bilan ADMIN/MIJOZ ga ajratildi, jimlik segmenti tashlandi
    assert [s["role"] for s in transcript["segments"]] == ["ADMIN", "MIJOZ", "ADMIN"]
    assert transcript["roleMethod"] == "ai"
    assert "Rahmat" not in " ".join(s["text"] for s in transcript["segments"])

    stt = [r for r in groq.requests if r["kind"] == "stt"]
    assert len(stt) == 1 and stt[0]["url"] == f"{config.GROQ_BASE_URL}/audio/transcriptions"
    body = stt[0]["body"]
    for field, value in (("model", b"whisper-large-v3-turbo"), ("language", b"uz"), ("response_format", b"verbose_json")):
        assert f'name="{field}"'.encode() in body and value in body
    labeling = [r for r in groq.requests if r.get("name") == "segment_roles"]
    assert labeling[0]["model"] == "openai/gpt-oss-20b"  # yordamchi vazifa yengil modelda

    an = client.post(f"/api/calls/{cid}/analyze", headers=env["mgr"])
    assert an.status_code == 200, an.text
    detail = an.json()
    assert detail["analysisStatus"] == "done"
    assert detail["analysis"]["provider"] == "groq"
    assert detail["analysis"]["model"] == "openai/gpt-oss-120b"
    assert detail["analysis"]["result"]["rating"]["label"] == "Yaxshi"
    analysis_req = [r for r in groq.requests if r.get("name") == "call_analysis"][0]
    assert analysis_req["model"] == "openai/gpt-oss-120b"
    assert analysis_req["format"]["json_schema"]["strict"] is True

    # Lead tarixi va statistika
    history = client.get(f"/api/leads/{env['lead']['id']}/calls", headers=env["mgr"]).json()
    assert any(c["id"] == cid and c["analysisStatus"] == "done" for c in history)
    stats = client.get("/api/calls/stats", headers=env["mgr"]).json()
    assert stats["config"] == {"stt": True, "analysis": True, "provider": "groq"}
    assert stats["totals"]["analyzed"] >= 1


def test_audio_over_limit_returns_clear_413(client, env, groq, monkeypatch):
    cid = _recorded_call(client, env)
    monkeypatch.setattr(config, "GROQ_STT_MAX_MB", 0)  # har qanday audio limitdan katta
    resp = client.post(f"/api/calls/{cid}/transcribe", headers=env["mgr"])
    assert resp.status_code == 413
    assert "MB" in resp.json()["detail"] and "saqlanib qoldi" in resp.json()["detail"]
    assert not [r for r in groq.requests if r["kind"] == "stt"]  # Groq'ga yuborilmadi
    detail = client.get(f"/api/calls/{cid}", headers=env["mgr"]).json()
    assert detail["transcriptStatus"] == "failed"
    assert detail["recording"]["available"] is True
    assert client.get(f"/api/calls/{cid}/recording", headers=env["mgr"]).status_code == 200


def test_rate_limit_returns_429_and_keeps_recording(client, env, groq):
    cid = _recorded_call(client, env)
    groq.stt_status = 429
    resp = client.post(f"/api/calls/{cid}/transcribe", headers=env["mgr"])
    assert resp.status_code == 429
    assert "limit" in resp.json()["detail"]
    groq.stt_status = 200
    retry = client.post(f"/api/calls/{cid}/transcribe", headers=env["mgr"])
    assert retry.status_code == 200, retry.text


def test_strict_schema_rejected_falls_back(client, env, groq):
    cid = _recorded_call(client, env)
    groq.reject_strict = True
    assert client.post(f"/api/calls/{cid}/transcribe", headers=env["mgr"]).status_code == 200
    an = client.post(f"/api/calls/{cid}/analyze", headers=env["mgr"])
    assert an.status_code == 200, an.text
    modes = [r["format"]["json_schema"]["strict"] for r in groq.requests if r.get("name") == "call_analysis"]
    assert modes == [True, False]


def test_role_labeling_failure_still_saves_transcript(client, env, groq):
    cid = _recorded_call(client, env)
    groq.label_fail = True
    tr = client.post(f"/api/calls/{cid}/transcribe", headers=env["mgr"])
    assert tr.status_code == 200, tr.text
    transcript = tr.json()["transcript"]
    assert transcript["roleMethod"] == "single" and transcript["warning"]
    assert "Turkiyaga" in transcript["segments"][0]["text"]


def test_not_configured_is_503_not_502(client, env, monkeypatch):
    cid = _recorded_call(client, env)
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    monkeypatch.setattr(config, "XAI_API_KEY", "")
    resp = client.post(f"/api/calls/{cid}/transcribe", headers=env["mgr"])
    assert resp.status_code == 503
    assert "GROQ_API_KEY" in resp.json()["detail"]
    cfg = client.get("/api/calls/config", headers=env["mgr"]).json()
    assert cfg["stt"] is False and cfg["analysis"] is False and cfg["provider"] is None


def test_config_reports_groq_without_leaking_key(client, env, groq):
    resp = client.get("/api/calls/config", headers=env["mgr"])
    data = resp.json()
    assert data["stt"] is True and data["analysis"] is True and data["provider"] == "groq"
    assert data["sttModel"] == "whisper-large-v3-turbo" and data["analysisModel"] == "openai/gpt-oss-120b"
    assert data["maxSttMb"] == 25
    assert KEY not in resp.text
    assert KEY not in client.get("/api/health").text
    assert client.get("/api/health").json()["callCenter"]["provider"] == "groq"
    # Saqlangan yozuvlarda ham kalit yo'q
    for name in ("calls", "call_transcripts", "call_analyses"):
        assert KEY not in json.dumps(storage.read(name))
