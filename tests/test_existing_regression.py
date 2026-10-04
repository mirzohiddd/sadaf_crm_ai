"""Mavjud CRM funksiyalari buzilmaganini tekshiruvchi regressiya testlari."""
from __future__ import annotations

from .conftest import login


def test_login_me_logout(client):
    boss = login(client, "boss", "boss12345")
    me = client.get("/api/auth/me", headers=boss).json()
    assert me["role"] == "super_admin"
    assert "calls" in me["permissions"]  # yangi resurs qo'shildi
    assert "leads" in me["permissions"]  # eskilari joyida
    assert client.post("/api/auth/logout", headers=boss).json() == {"ok": True}
    assert client.post("/api/auth/login", json={"login": "boss", "password": "wrong"}).status_code == 401


def test_lead_crud_stage_comments_reminders(client):
    boss = login(client, "boss", "boss12345")
    lead = client.post("/api/leads", headers=boss, json={"name": "Regress", "phone": "+998 91 111 11 11",
                                                         "manager": "Bosh Menejer"}).json()
    lid = lead["id"]
    assert client.patch(f"/api/leads/{lid}/stage", headers=boss, json={"stage": "Bog'lanildi"}).status_code == 200
    assert client.post(f"/api/leads/{lid}/comments", headers=boss, json={"text": "test"}).status_code == 201
    rem = client.post(f"/api/leads/{lid}/reminders", headers=boss,
                      json={"date": "2030-01-01", "time": "10:00", "note": "x"})
    assert rem.status_code in (200, 201)
    assert client.get(f"/api/leads/{lid}/reminders", headers=boss).status_code == 200
    assert client.get("/api/leads/new-count", headers=boss).status_code == 200
    assert client.get("/api/leads/assignment", headers=boss).status_code == 200
    assert client.delete(f"/api/leads/{lid}", headers=boss).status_code == 200


def test_other_endpoints_still_work(client):
    boss = login(client, "boss", "boss12345")
    for path in ("/api/health", "/api/dashboard", "/api/analytics", "/api/activity", "/api/notifications",
                 "/api/notifications/unread-count", "/api/tasks", "/api/clients", "/api/sales",
                 "/api/tours", "/api/employees", "/api/settings", "/api/ai/status", "/api/ai/history"):
        resp = client.get(path, headers=boss)
        assert resp.status_code == 200, (path, resp.text)


def test_existing_ai_assistant_still_works(client):
    boss = login(client, "boss", "boss12345")
    resp = client.post("/api/ai/chat", headers=boss, json={"message": "salom", "history": []})
    assert resp.status_code == 200
    assert resp.json()["lines"]
