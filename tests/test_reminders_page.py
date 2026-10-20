"""Umumiy "Eslatmalar" sahifasi (GET/POST /api/reminders) testlari.

Tekshiriladi:
- lead ichida qo'shilgan eslatma umumiy ro'yxatda ham chiqadi (bitta manba);
- umumiy sahifadan qo'shilgan eslatma lead ichida ham chiqadi;
- bir xil eslatma ikki marta yaratilmaydi (dublikat yo'q);
- ko'rish doirasi: menejer faqat o'z leadlari eslatmalarini ko'radi;
- o'chirish ruxsati: begona menejer o'chira olmaydi (403), egasi/Bosh menejer o'chira oladi;
- o'chirilgan eslatma qayta o'qilganda qaytib chiqmaydi;
- lead o'chirilsa — uning eslatmalari ham o'chadi;
- "eslatmalar" sahifasi barcha rollar uchun ochiq.
"""
from __future__ import annotations

import pytest

from .conftest import login


@pytest.fixture(scope="module")
def env(client):
    boss = login(client, "boss", "boss12345")
    for n in (1, 2):
        resp = client.post(
            "/api/employees",
            headers=boss,
            json={"name": f"Eslatma Menejer {n}", "crmLogin": f"remmgr{n}", "crmPassword": "secret12",
                  "crmRole": "admin"},
        )
        assert resp.status_code == 201, resp.text
    m1 = login(client, "remmgr1", "secret12")
    m2 = login(client, "remmgr2", "secret12")
    # Oddiy Admin yaratgan lead avtomatik o'ziga biriktiriladi
    lead1 = client.post("/api/leads", headers=m1, json={"name": "Aziz Eslatma", "phone": "+998 90 123 45 01"}).json()
    lead2 = client.post("/api/leads", headers=m2, json={"name": "Bobur Eslatma", "phone": "+998 90 123 45 02"}).json()
    return {"boss": boss, "m1": m1, "m2": m2, "lead1": lead1, "lead2": lead2}


def _ids(rows):
    return [r["id"] for r in rows]


def test_page_is_open_for_all_roles(client, env):
    for headers in (env["boss"], env["m1"]):
        me = client.get("/api/auth/me", headers=headers).json()
        assert "eslatmalar" in me["pages"]


def test_lead_reminder_appears_in_global_list(client, env):
    lid = env["lead1"]["id"]
    created = client.post(f"/api/leads/{lid}/reminders", headers=env["m1"],
                          json={"date": "2031-05-01", "time": "10:30", "note": "Qo'ng'iroq qilish"})
    assert created.status_code == 201, created.text
    rid = created.json()["id"]

    rows = client.get("/api/reminders", headers=env["m1"]).json()
    row = next(r for r in rows if r["id"] == rid)
    assert row["leadId"] == lid
    assert row["leadName"] == "Aziz Eslatma"
    assert row["note"] == "Qo'ng'iroq qilish"
    assert row["date"] == "2031-05-01" and row["time"] == "10:30"
    assert row["canManage"] is True
    assert _ids(rows).count(rid) == 1


def test_create_from_page_appears_in_lead(client, env):
    lid = env["lead1"]["id"]
    resp = client.post("/api/reminders", headers=env["m1"],
                       json={"leadId": lid, "date": "2031-06-02", "time": "09:00", "note": "Taklif yuborish"})
    assert resp.status_code == 201, resp.text
    rid = resp.json()["id"]
    assert resp.json()["leadName"] == "Aziz Eslatma"
    lead_rows = client.get(f"/api/leads/{lid}/reminders", headers=env["m1"]).json()
    assert rid in _ids(lead_rows)


def test_duplicate_is_not_created(client, env):
    lid = env["lead1"]["id"]
    body = {"leadId": lid, "date": "2031-07-03", "time": "11:00", "note": "Dublikat test"}
    a = client.post("/api/reminders", headers=env["m1"], json=body).json()
    b = client.post("/api/reminders", headers=env["m1"], json=body).json()
    c = client.post(f"/api/leads/{lid}/reminders", headers=env["m1"], json=body).json()
    assert a["id"] == b["id"] == c["id"]
    rows = client.get("/api/reminders", headers=env["m1"]).json()
    assert sum(1 for r in rows if r["note"] == "Dublikat test") == 1


def test_validation_and_access(client, env):
    # Noto'g'ri sana
    bad = client.post("/api/reminders", headers=env["m1"],
                      json={"leadId": env["lead1"]["id"], "date": "2031-13-45", "time": "10:00"})
    assert bad.status_code == 400
    # Mavjud bo'lmagan lead
    assert client.post("/api/reminders", headers=env["m1"],
                       json={"leadId": 999999, "date": "2031-01-01"}).status_code == 404
    # Begona leadga eslatma qo'yib bo'lmaydi
    assert client.post("/api/reminders", headers=env["m2"],
                       json={"leadId": env["lead1"]["id"], "date": "2031-01-01"}).status_code == 403
    # Tokensiz
    assert client.get("/api/reminders").status_code == 401


def test_scoping(client, env):
    r2 = client.post(f"/api/leads/{env['lead2']['id']}/reminders", headers=env["m2"],
                     json={"date": "2031-08-04", "time": "15:00", "note": "M2 eslatmasi"}).json()
    m1_rows = client.get("/api/reminders", headers=env["m1"]).json()
    m2_rows = client.get("/api/reminders", headers=env["m2"]).json()
    boss_rows = client.get("/api/reminders", headers=env["boss"]).json()
    assert r2["id"] not in _ids(m1_rows)
    assert r2["id"] in _ids(m2_rows)
    assert all(r["leadId"] == env["lead2"]["id"] for r in m2_rows)
    assert r2["id"] in _ids(boss_rows)
    assert all(r["canManage"] for r in boss_rows)


def test_delete_permissions_and_persistence(client, env):
    lid = env["lead2"]["id"]
    rid = client.post(f"/api/leads/{lid}/reminders", headers=env["m2"],
                      json={"date": "2031-09-05", "time": "12:00", "note": "O'chiriladi"}).json()["id"]
    # Begona menejer o'chira olmaydi
    assert client.delete(f"/api/reminders/{rid}", headers=env["m1"]).status_code == 403
    # Egasi o'chiradi
    assert client.delete(f"/api/reminders/{rid}", headers=env["m2"]).status_code == 200
    # Qayta o'qilganda (sahifa yangilanganda) qaytib chiqmaydi
    assert rid not in _ids(client.get("/api/reminders", headers=env["m2"]).json())
    assert rid not in _ids(client.get("/api/reminders", headers=env["boss"]).json())
    assert rid not in _ids(client.get(f"/api/leads/{lid}/reminders", headers=env["m2"]).json())
    # Ikkinchi marta o'chirish — 404
    assert client.delete(f"/api/reminders/{rid}", headers=env["m2"]).status_code == 404


def test_lead_delete_removes_its_reminders(client, env):
    boss = env["boss"]
    lead = client.post("/api/leads", headers=env["m1"], json={"name": "Vaqtinchalik", "phone": "+998 90 123 45 09"}).json()
    rid = client.post(f"/api/leads/{lead['id']}/reminders", headers=env["m1"],
                      json={"date": "2031-10-06", "time": "08:00", "note": "yetim"}).json()["id"]
    assert client.delete(f"/api/leads/{lead['id']}", headers=boss).status_code == 200
    assert rid not in _ids(client.get("/api/reminders", headers=boss).json())
    # Boshqa leadlarning eslatmalari joyida qoladi
    assert any(r["leadId"] == env["lead1"]["id"] for r in client.get("/api/reminders", headers=boss).json())
