"""AI Call Center testlari — topshiriqdagi TEST 1–7 + xavfsizlik.

xAI tarmoq chaqiruvlari mock qilinadi (haqiqiy kalit/internet kerak emas).
"""
from __future__ import annotations

from typing import Any

import pytest

from app import config, storage
from app.services import xai_service
from app.services.xai_service import XaiError

from .conftest import login, make_wav

STT_RESULT = {
    "text": "Allo. Assalomu alaykum, SADAF turagentligidan. Turkiyaga tur kerak edi. Narxi 900 dollar.",
    "language": "uz",
    "duration": 12.5,
    "words": [
        {"text": "Allo.", "start": 0.1, "end": 0.5, "speaker": 1},
        {"text": "Assalomu", "start": 0.8, "end": 1.2, "speaker": 0},
        {"text": "alaykum,", "start": 1.2, "end": 1.6, "speaker": 0},
        {"text": "SADAF", "start": 1.7, "end": 2.0, "speaker": 0},
        {"text": "turagentligidan.", "start": 2.0, "end": 2.8, "speaker": 0},
        {"text": "Turkiyaga", "start": 3.0, "end": 3.5, "speaker": 1},
        {"text": "tur", "start": 3.5, "end": 3.7, "speaker": 1},
        {"text": "kerak", "start": 3.7, "end": 4.0, "speaker": 1},
        {"text": "edi.", "start": 4.0, "end": 4.2, "speaker": 1},
        {"text": "Narxi", "start": 5.0, "end": 5.4, "speaker": 0},
        {"text": "900", "start": 5.4, "end": 5.8, "speaker": 0},
        {"text": "dollar.", "start": 5.8, "end": 6.2, "speaker": 0},
    ],
}


def _check(keys: list[str], fail: set[str]) -> dict[str, Any]:
    return {k: {"status": "fail" if k in fail else "pass", "comment": "izoh"} for k in keys}


ANALYSIS_RESULT = {
    "overall_rating": "good",
    "overall_score": 78,
    "summary": "Muloqot yaxshi, lekin follow-up belgilanmagan.",
    "communication_rating": "good",
    "communication": _check(
        ["greeting", "respect", "clarity", "answered_questions", "manners", "no_rude_language"], set()
    ),
    "sales_rating": "average",
    "sales": _check(
        ["needs", "budget", "interest", "product_explained", "price_explained",
         "benefits_explained", "objection_handling", "cta", "follow_up"],
        {"cta", "follow_up"},
    ),
    "lead": {
        "interest": "Turkiya turi", "main_need": "Oilaviy dam olish", "main_problem": "Narx",
        "budget": "Aniqlanmagan", "travel_date": "Aniqlanmagan", "sales_opportunity": "high",
        "sales_opportunity_reason": "Qiziqish yuqori", "next_step": "Taklif yuborish",
    },
    "problems": ["Mijozning safar sanasi aniqlanmagan", "Keyingi aloqa vaqti belgilanmagan"],
    "recommendations": ["Mijozdan safar sanasini so'rash", "Follow-up vaqtini belgilash"],
    "inappropriate_phrases": [],
    "follow_up": {"agreed": False, "when": ""},
}


@pytest.fixture(autouse=True)
def mock_xai(monkeypatch):
    """Standart holatda xAI muvaffaqiyatli javob beradi."""

    async def fake_transcribe(path, filename, mime, *, keyterms=None):
        assert path.exists()
        return STT_RESULT

    async def fake_chat_json(system, user, schema, schema_name, *, temperature=0.2):
        if schema_name == "speaker_roles":
            return {"agent_speaker": 0}
        # Modelga telefon raqami yuborilmasligi kerak (maxfiylik)
        assert "+998" not in user
        return ANALYSIS_RESULT

    monkeypatch.setattr(xai_service, "transcribe", fake_transcribe)
    monkeypatch.setattr(xai_service, "chat_json", fake_chat_json)


@pytest.fixture(scope="module")
def env(client):
    """Bosh menejer + 2 menejer (admin roli) + round-robin bo'yicha 4 lead."""
    boss = login(client, "boss", "boss12345")
    ids = {}
    for n in (1, 2):
        resp = client.post(
            "/api/employees",
            headers=boss,
            json={"name": f"Manager {n}", "crmLogin": f"manager{n}", "crmPassword": "secret12",
                  "crmRole": "admin"},
        )
        assert resp.status_code == 201, resp.text
        ids[n] = resp.json()["id"]
    m1 = login(client, "manager1", "secret12")
    m2 = login(client, "manager2", "secret12")

    leads = []
    for i in range(1, 5):
        resp = client.post("/api/leads", headers=boss,
                           json={"name": f"Lead {i}", "phone": f"+998 90 000 00 0{i}", "source": "Instagram"})
        assert resp.status_code == 201, resp.text
        leads.append(resp.json())
    return {"boss": boss, "m1": m1, "m2": m2, "ids": ids, "leads": leads}


def _make_call(client, headers, lead_id, **extra) -> dict[str, Any]:
    resp = client.post("/api/calls", headers=headers, json={"leadId": lead_id, "consent": True, **extra})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _upload(client, headers, call_id, data=None, duration=1.0):
    return client.post(
        f"/api/calls/{call_id}/recording",
        headers=headers,
        files={"file": ("call.wav", data if data is not None else make_wav(), "audio/wav")},
        data={"duration": str(duration)},
    )


# ——— Mavjud round-robin buzilmaganini tekshirish ———


def test_round_robin_assignment_unchanged(env):
    managers = [lead["manager"] for lead in env["leads"]]
    assert managers == ["Manager 1", "Manager 2", "Manager 1", "Manager 2"]


# ——— TEST 1 / TEST 2: manager isolation (leadlar) ———


def test_1_manager1_sees_only_own_leads(client, env):
    names = {lead["name"] for lead in client.get("/api/leads", headers=env["m1"]).json()}
    assert names == {"Lead 1", "Lead 3"}


def test_2_manager2_sees_only_own_leads(client, env):
    names = {lead["name"] for lead in client.get("/api/leads", headers=env["m2"]).json()}
    assert names == {"Lead 2", "Lead 4"}


# ——— TEST 4: call → audio → transcript → analiz Lead 1 ga bog'lanadi ———


def test_4_full_call_pipeline_linked_to_lead(client, env):
    lead1 = env["leads"][0]
    # managerId ni soxtalashtirishga urinish — e'tiborsiz qoladi
    call = _make_call(client, env["m1"], lead1["id"], managerId=env["ids"][2])
    assert call["managerId"] == env["ids"][1]
    assert call["leadId"] == lead1["id"]
    assert call["status"] == "created"

    up = _upload(client, env["m1"], call["id"], make_wav(2.0))
    assert up.status_code == 200, up.text
    assert up.json()["recording"]["available"] is True
    assert up.json()["duration"] == pytest.approx(2.0, abs=0.05)
    assert "file" not in up.json()["recording"]  # fayl nomi/yo'li tashqariga chiqmaydi

    tr = client.post(f"/api/calls/{call['id']}/transcribe", headers=env["m1"])
    assert tr.status_code == 200, tr.text
    transcript = tr.json()["transcript"]
    assert transcript["leadId"] == lead1["id"]
    roles = [s["role"] for s in transcript["segments"]]
    assert roles == ["MIJOZ", "ADMIN", "MIJOZ", "ADMIN"]
    assert transcript["roleMethod"] == "ai"

    an = client.post(f"/api/calls/{call['id']}/analyze", headers=env["m1"])
    assert an.status_code == 200, an.text
    detail = an.json()
    assert detail["analysis"]["leadId"] == lead1["id"]
    assert detail["analysisStatus"] == "done"
    result = detail["analysis"]["result"]
    assert result["rating"]["label"] == "Yaxshi"
    sales = {i["key"]: i["status"] for i in result["sales"]["items"]}
    assert sales["cta"] == "fail" and sales["follow_up"] == "fail" and sales["needs"] == "pass"
    assert "Mijozning safar sanasi aniqlanmagan" in result["problems"]
    assert detail["analysisSummary"]["problems"] == 2

    audio = client.get(f"/api/calls/{call['id']}/recording", headers=env["m1"])
    assert audio.status_code == 200
    assert audio.content[:4] == b"RIFF"
    assert "no-store" in audio.headers["cache-control"]
    env["call1"] = call["id"]


# ——— TEST 5: yangi call — oldingisi o'zgarmaydi ———


def test_5_new_call_does_not_modify_previous(client, env):
    before = client.get(f"/api/calls/{env['call1']}", headers=env["m1"]).json()

    # Xuddi shu lead bilan yana suhbat
    again = _make_call(client, env["m1"], env["leads"][0]["id"])
    assert again["id"] != env["call1"]
    assert _upload(client, env["m1"], again["id"]).status_code == 200
    assert client.post(f"/api/calls/{again['id']}/transcribe", headers=env["m1"]).status_code == 200

    # Boshqa lead (Lead 3 — ham Manager 1 niki) bilan suhbat
    other = _make_call(client, env["m1"], env["leads"][2]["id"])
    assert _upload(client, env["m1"], other["id"]).status_code == 200

    after = client.get(f"/api/calls/{env['call1']}", headers=env["m1"]).json()
    assert after == before
    env["call2"] = again["id"]
    env["call3"] = other["id"]


def test_recording_is_immutable(client, env):
    resp = _upload(client, env["m1"], env["call1"])
    assert resp.status_code == 409


# ——— TEST 6: Lead detail — barcha call history ———


def test_6_lead_call_history(client, env):
    rows = client.get(f"/api/leads/{env['leads'][0]['id']}/calls", headers=env["m1"]).json()
    assert [r["id"] for r in rows] == [env["call2"], env["call1"]]  # eng yangisi birinchi
    assert all(r["leadId"] == env["leads"][0]["id"] for r in rows)


# ——— TEST 7: Grok ishlamasa — recording/transcript yo'qolmaydi ———


def test_7_grok_failure_keeps_data(client, env, monkeypatch):
    async def broken(*args, **kwargs):
        raise XaiError("xAI xizmati vaqtincha ishlamayapti (HTTP 503). Keyinroq qayta urinib ko'ring.",
                       "xai_unavailable", 503)

    monkeypatch.setattr(xai_service, "chat_json", broken)
    call_id = env["call2"]
    resp = client.post(f"/api/calls/{call_id}/analyze", headers=env["m1"])
    assert resp.status_code == 502
    assert "vaqtincha ishlamayapti" in resp.json()["detail"]
    assert "test-xai-key-SECRET" not in resp.text

    detail = client.get(f"/api/calls/{call_id}", headers=env["m1"]).json()
    assert detail["analysisStatus"] == "failed"
    assert "vaqtincha" in detail["analysisError"]
    assert detail["transcriptStatus"] == "done" and detail["transcript"]["segments"]
    assert detail["recording"]["available"] is True
    assert client.get(f"/api/calls/{call_id}/recording", headers=env["m1"]).status_code == 200

    # Grok qayta ishlaganda — retry muvaffaqiyatli
    monkeypatch.undo()

    async def ok(system, user, schema, schema_name, *, temperature=0.2):
        return ANALYSIS_RESULT

    monkeypatch.setattr(xai_service, "chat_json", ok)
    retry = client.post(f"/api/calls/{call_id}/analyze", headers=env["m1"])
    assert retry.status_code == 200
    assert retry.json()["analysisStatus"] == "done"


def test_stt_failure_keeps_recording(client, env, monkeypatch):
    async def broken(*args, **kwargs):
        raise XaiError("Speech-to-text sozlanmagan: .env faylga XAI_API_KEY qo'shing.", "xai_not_configured")

    monkeypatch.setattr(xai_service, "transcribe", broken)
    resp = client.post(f"/api/calls/{env['call3']}/transcribe", headers=env["m1"])
    # Sozlanmagan STT — bu tashqi server xatosi emas: 502 emas, 503 + tushunarli xabar
    assert resp.status_code == 503
    assert "sozlanmagan" in resp.json()["detail"]
    detail = client.get(f"/api/calls/{env['call3']}", headers=env["m1"]).json()
    assert detail["transcriptStatus"] == "failed"
    assert detail["recording"]["available"] is True


# ——— Xavfsizlik: Manager 2 Manager 1 ma'lumotini ko'ra olmaydi ———


def test_manager2_cannot_access_manager1_calls(client, env):
    cid = env["call1"]
    m2 = env["m2"]
    assert client.get(f"/api/calls/{cid}", headers=m2).status_code == 403
    assert client.get(f"/api/calls/{cid}/recording", headers=m2).status_code == 403
    assert client.get(f"/api/calls/{cid}/transcript", headers=m2).status_code == 403
    assert client.get(f"/api/calls/{cid}/analysis", headers=m2).status_code == 403
    assert client.post(f"/api/calls/{cid}/analyze", headers=m2).status_code == 403
    assert client.post(f"/api/calls/{cid}/transcribe", headers=m2).status_code == 403
    assert client.delete(f"/api/calls/{cid}", headers=m2).status_code == 403
    assert client.get(f"/api/leads/{env['leads'][0]['id']}/calls", headers=m2).status_code == 403
    # managerId query parametri bilan chetlab o'tish mumkin emas
    rows = client.get("/api/calls", headers=m2, params={"managerId": env["ids"][1]}).json()
    assert rows == []
    rows = client.get("/api/calls", headers=m2, params={"leadId": env["leads"][0]["id"]}).json()
    assert rows == []
    # Boshqa menejer leadiga qo'ng'iroq ochib bo'lmaydi
    resp = client.post("/api/calls", headers=m2, json={"leadId": env["leads"][0]["id"], "consent": True})
    assert resp.status_code == 403


def test_manager1_list_contains_only_own(client, env):
    rows = client.get("/api/calls", headers=env["m1"]).json()
    assert {r["managerId"] for r in rows} == {env["ids"][1]}
    assert len(rows) == 3


def test_unauthenticated_rejected(client, env):
    assert client.get("/api/calls").status_code == 401
    assert client.get(f"/api/calls/{env['call1']}/recording").status_code == 401
    assert client.post("/api/calls", json={"leadId": 1, "consent": True}).status_code == 401


def test_consent_required(client, env):
    resp = client.post("/api/calls", headers=env["m2"], json={"leadId": env["leads"][1]["id"]})
    assert resp.status_code == 400


def test_non_audio_upload_rejected(client, env):
    call = _make_call(client, env["m2"], env["leads"][1]["id"])
    resp = _upload(client, env["m2"], call["id"], b"<html>" + b"x" * 4000)
    assert resp.status_code == 415
    # Bekor qilingan (audiosiz) qoralamani egasi o'chira oladi
    assert client.delete(f"/api/calls/{call['id']}", headers=env["m2"]).status_code == 200


def test_upload_size_limit(client, env):
    call = _make_call(client, env["m2"], env["leads"][1]["id"])
    big = make_wav(1.0) + b"\0" * (6 * 1024 * 1024)
    resp = _upload(client, env["m2"], call["id"], big)
    assert resp.status_code == 413
    assert not list(config.RECORDINGS_DIR.glob("*.part"))
    client.delete(f"/api/calls/{call['id']}", headers=env["m2"])


def test_manager_cannot_delete_recorded_call(client, env):
    assert client.delete(f"/api/calls/{env['call1']}", headers=env["m1"]).status_code == 403


def test_swap_roles(client, env):
    resp = client.post(f"/api/calls/{env['call1']}/transcript/swap-roles", headers=env["m1"])
    assert resp.status_code == 200
    detail = resp.json()
    assert [s["role"] for s in detail["transcript"]["segments"]] == ["ADMIN", "MIJOZ", "ADMIN", "MIJOZ"]
    assert detail["analysisOutdated"] is True
    client.post(f"/api/calls/{env['call1']}/transcript/swap-roles", headers=env["m1"])


# ——— TEST 3: Bosh menejer hammasini ko'radi + statistika real ———


def test_3_boss_sees_everything(client, env):
    boss = env["boss"]
    assert len(client.get("/api/leads", headers=boss).json()) == 4

    # Manager 2 ham bitta to'liq qo'ng'iroq qiladi
    call = _make_call(client, env["m2"], env["leads"][1]["id"])
    _upload(client, env["m2"], call["id"])
    client.post(f"/api/calls/{call['id']}/transcribe", headers=env["m2"])
    client.post(f"/api/calls/{call['id']}/analyze", headers=env["m2"])

    rows = client.get("/api/calls", headers=boss).json()
    assert {r["managerId"] for r in rows} == {env["ids"][1], env["ids"][2]}
    assert client.get(f"/api/calls/{env['call1']}", headers=boss).status_code == 200
    assert client.get(f"/api/calls/{env['call1']}/recording", headers=boss).status_code == 200
    only_m2 = client.get("/api/calls", headers=boss, params={"managerId": env["ids"][2]}).json()
    assert [r["id"] for r in only_m2] == [call["id"]]

    stats = client.get("/api/calls/stats", headers=boss).json()
    assert stats["scope"] == "all"
    by_name = {m["name"]: m for m in stats["managers"]}
    assert by_name["Manager 1"]["calls"] == 3
    assert by_name["Manager 1"]["analyzed"] == 2
    assert by_name["Manager 1"]["problemsFound"] == 4
    assert by_name["Manager 1"]["pendingAnalysis"] == 1
    assert by_name["Manager 2"]["calls"] == 1
    assert by_name["Manager 2"]["analyzed"] == 1
    assert stats["totals"]["calls"] == 4
    assert stats["totals"]["todayCalls"] == 4
    assert stats["totals"]["missedFollowUps"] == 3

    own = client.get("/api/calls/stats", headers=env["m2"]).json()
    assert own["scope"] == "own"
    assert own["totals"]["calls"] == 1
    assert [m["name"] for m in own["managers"]] == ["Manager 2"]


def test_lead_reassignment_moves_call_visibility(client, env):
    """Lead Bosh menejer tomonidan boshqa menejerga o'tkazilsa: yangi egasi
    lead tarixini ko'radi, eski menejer esa faqat o'zi qilgan qo'ng'iroqlarni."""
    lead3 = env["leads"][2]
    resp = client.put(f"/api/leads/{lead3['id']}", headers=env["boss"], json={**lead3, "manager": "Manager 2"})
    assert resp.status_code == 200
    m2_rows = client.get(f"/api/leads/{lead3['id']}/calls", headers=env["m2"]).json()
    assert [r["id"] for r in m2_rows] == [env["call3"]]
    assert client.get(f"/api/calls/{env['call3']}", headers=env["m1"]).status_code == 200


def test_boss_can_delete_and_audio_is_removed(client, env):
    call = _make_call(client, env["m2"], env["leads"][3]["id"])
    _upload(client, env["m2"], call["id"])
    stored = storage.get_one("calls", call["id"])["recording"]["file"]
    assert (config.RECORDINGS_DIR / stored).exists()
    assert client.delete(f"/api/calls/{call['id']}", headers=env["boss"]).status_code == 200
    assert not (config.RECORDINGS_DIR / stored).exists()
    assert client.get(f"/api/calls/{call['id']}", headers=env["boss"]).status_code == 404


def test_config_endpoint_never_leaks_key(client, env):
    resp = client.get("/api/calls/config", headers=env["m1"])
    assert resp.status_code == 200
    assert resp.json()["analysis"] is True
    assert "test-xai-key-SECRET" not in resp.text
    me = client.get("/api/auth/me", headers=env["m1"]).text
    assert "test-xai-key-SECRET" not in me


def test_lead_delete_keeps_archive_for_boss(client, env):
    """Lead o'chirilsa ham qo'ng'iroq arxivi yo'qolmaydi (Bosh menejer ko'radi)."""
    lead4 = env["leads"][3]
    call = _make_call(client, env["m2"], lead4["id"])
    _upload(client, env["m2"], call["id"])
    assert client.delete(f"/api/leads/{lead4['id']}", headers=env["boss"]).status_code == 200
    detail = client.get(f"/api/calls/{call['id']}", headers=env["boss"])
    assert detail.status_code == 200
    assert detail.json()["leadName"] == "Lead 4"
    # Qo'ng'iroqni qilgan menejer ham o'z ishini ko'radi; begona menejer — yo'q
    assert client.get(f"/api/calls/{call['id']}", headers=env["m2"]).status_code == 200
    assert client.get(f"/api/calls/{call['id']}", headers=env["m1"]).status_code == 403
