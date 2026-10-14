"""Lead round-robin: mavjud leadlarni qayta taqsimlash va yangi leadlar navbati."""
from __future__ import annotations

import json

import pytest

from app import config, storage
from app.security import hash_password
from app.services import assignment

from .conftest import login

SHEETS = {"X-Sheets-Secret": "rr-secret"}


def _user(uid, login_, name, role, active=True):
    return {"id": uid, "login": login_, "name": name, "role": role, "active": active,
            "passwordHash": hash_password("secret12"), "managerId": None}


@pytest.fixture()
def iso(tmp_path, monkeypatch, client):
    """Alohida bo'sh ma'lumotlar papkasi: Bosh menejer + 2 faol menejer + 1 faolsiz + 1 operator."""
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "ROUND_ROBIN_EXCLUDE_LOGINS", set())
    monkeypatch.setattr("app.routers.integrations.SHEETS_WEBHOOK_SECRET", "rr-secret")
    storage.write("users", [
        _user(1, "boss", "Bosh Menejer", "super_admin"),
        _user(2, "m1", "Menejer 1", "admin"),
        _user(3, "m2", "Menejer 2", "admin"),
        _user(4, "old", "Ketgan Menejer", "admin", active=False),
        _user(5, "op", "Operator", "operator"),
    ])
    return {"boss": login(client, "boss", "secret12"), "m1": login(client, "m1", "secret12")}


def _seed_leads(n, manager="Bosh Menejer"):
    storage.write("leads", [
        {"id": i, "name": f"Lead {i}", "phone": f"+99890{i:07d}", "manager": manager, "stage": "Yangi",
         "source": "Instagram", "comment": f"izoh {i}", "amount": 100 + i, "ownerId": None,
         "createdAt": "2026-01-01T00:00:00+00:00", "updatedAt": "2026-01-01T00:00:00+00:00"}
        for i in range(1, n + 1)
    ])


def test_active_managers_exclude_boss_inactive_and_operators(iso):
    # Bosh menejer navbatga kirmaydi — leadlar faqat hodimlarga tushadi
    assert [a["name"] for a in assignment.active_admins()] == ["Menejer 1", "Menejer 2"]


def test_rebalance_840_leads_evenly_in_order(client, iso):
    _seed_leads(840)
    before = {l["id"]: l for l in storage.read("leads")}
    resp = client.post("/api/leads/rebalance", headers=iso["boss"])
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["success"] is True and data["total"] == 840
    assert [(m["name"], m["leads"]) for m in data["managers"]] == [("Menejer 1", 420), ("Menejer 2", 420)]

    leads = sorted(storage.read("leads"), key=lambda l: l["id"])
    assert [l["manager"] for l in leads[:6]] == ["Menejer 1", "Menejer 2"] * 3
    assert all(l["manager"] != "Bosh Menejer" for l in leads)
    # Faqat manager (va o'zgargan leadda updatedAt) o'zgaradi
    for lead in leads:
        old = before[lead["id"]]
        for key in ("name", "phone", "stage", "source", "comment", "amount", "ownerId", "createdAt"):
            assert lead[key] == old[key]
    assert len(leads) == 840                                   # yangi lead/dublikat yo'q
    # Lead 840 → Menejer 2 bo'ldi, keyingisi — navbat boshidan
    assert data["nextManager"]["name"] == "Menejer 1"


def test_uneven_count_differs_by_at_most_one(client, iso):
    _seed_leads(11)
    data = client.post("/api/leads/rebalance", headers=iso["boss"]).json()
    assert [m["leads"] for m in data["managers"]] == [6, 5]
    assert data["nextManager"]["name"] == "Menejer 2"          # lead 11 → Menejer 1


def test_new_sheets_leads_continue_queue_after_rebalance(client, iso, tmp_path):
    _seed_leads(6)                                             # oxirgisi → Menejer 2
    client.post("/api/leads/rebalance", headers=iso["boss"])
    got = []
    for i in range(4):
        resp = client.post("/api/integrations/sheets/lead", headers=SHEETS,
                           json={"name": f"Yangi {i}", "phone": f"+99891{i:07d}", "externalId": f"rr-{i}"})
        assert resp.status_code == 201, resp.text
        got.append(resp.json()["lead"]["manager"])
    assert got == ["Menejer 1", "Menejer 2", "Menejer 1", "Menejer 2"]
    # Navbat faylda saqlanadi — restartdan keyin ham shu joydan davom etadi
    state = json.loads((tmp_path / "lead_assignment.json").read_text(encoding="utf-8"))
    assert state[0]["lastAdminId"] == 3


def test_sheets_duplicate_does_not_consume_queue(client, iso):
    client.post("/api/integrations/sheets/lead", headers=SHEETS, json={"name": "A", "phone": "+998901110000"})
    dup = client.post("/api/integrations/sheets/lead", headers=SHEETS, json={"name": "A", "phone": "998901110000"})
    assert dup.json()["duplicate"] is True
    nxt = client.post("/api/integrations/sheets/lead", headers=SHEETS, json={"name": "B", "phone": "+998902220000"})
    assert nxt.json()["lead"]["manager"] == "Menejer 2"        # A → Menejer 1, B → Menejer 2
    assert len(storage.read("leads")) == 2


def test_sheets_manager_column_respected_when_it_matches_active_manager(client, iso):
    ok = client.post("/api/integrations/sheets/lead", headers=SHEETS,
                     json={"name": "C", "phone": "+998903330000", "manager": "menejer 2"}).json()["lead"]
    assert ok["manager"] == "Menejer 2"
    unknown = client.post("/api/integrations/sheets/lead", headers=SHEETS,
                          json={"name": "D", "phone": "+998904440000", "manager": "Ketgan Menejer"}).json()["lead"]
    assert unknown["manager"] == "Menejer 1"                   # faolsiz → navbat bo'yicha
    boss = client.post("/api/integrations/sheets/lead", headers=SHEETS,
                       json={"name": "E", "phone": "+998905550000", "manager": "Bosh Menejer"}).json()["lead"]
    assert boss["manager"] == "Menejer 2"                      # Bosh menejer ustunda bo'lsa ham → hodimga


def test_dry_run_changes_nothing(client, iso):
    _seed_leads(9)
    before = storage.read("leads")
    data = client.post("/api/leads/rebalance", headers=iso["boss"], json={"dryRun": True}).json()
    assert data["dryRun"] is True and [m["leads"] for m in data["managers"]] == [5, 4]
    assert data["changed"] == 9                                # hammasi Bosh Menejerdan hodimlarga o'tadi
    assert storage.read("leads") == before
    assert assignment.peek_next_admin()["name"] == "Menejer 1"


def test_rebalance_backup_saved(client, iso):
    _seed_leads(3, manager="Eski")
    client.post("/api/leads/rebalance", headers=iso["boss"])
    state = storage.get_one("lead_assignment", 1)
    assert state["rebalanceBackup"] == {"1": "Eski", "2": "Eski", "3": "Eski"}
    assert state["lastRebalance"]["total"] == 3


def test_managers_see_their_share_after_rebalance(client, iso):
    _seed_leads(9)
    client.post("/api/leads/rebalance", headers=iso["boss"])
    mine = client.get("/api/leads", headers=iso["m1"]).json()
    assert len(mine) == 5 and {l["manager"] for l in mine} == {"Menejer 1"}
    # Bosh menejer hamma hodimlarning leadlarini ko'radi
    assert len(client.get("/api/leads", headers=iso["boss"]).json()) == 9


def test_rebalance_requires_boss(client, iso):
    _seed_leads(3)
    assert client.post("/api/leads/rebalance").status_code == 401
    assert client.post("/api/leads/rebalance", headers=iso["m1"]).status_code == 403
    assert {l["manager"] for l in storage.read("leads")} == {"Bosh Menejer"}


def test_excluded_login_not_in_queue(client, iso, monkeypatch):
    monkeypatch.setattr(config, "ROUND_ROBIN_EXCLUDE_LOGINS", {"boss"})
    _seed_leads(4)
    data = client.post("/api/leads/rebalance", headers=iso["boss"]).json()
    assert [(m["name"], m["leads"]) for m in data["managers"]] == [("Menejer 1", 2), ("Menejer 2", 2)]


def test_no_active_managers_returns_409(client, iso, monkeypatch):
    monkeypatch.setattr(config, "ROUND_ROBIN_EXCLUDE_LOGINS", {"boss", "m1", "m2"})
    _seed_leads(2)
    assert client.post("/api/leads/rebalance", headers=iso["boss"]).status_code == 409


# ——— Bosh menejerga lead tushmaydi, u faqat kuzatadi ———


def test_boss_created_lead_goes_to_employee_not_boss(client, iso):
    got = [client.post("/api/leads", headers=iso["boss"],
                       json={"name": f"Q{i}", "phone": f"+99893{i:07d}"}).json()["manager"] for i in range(4)]
    assert got == ["Menejer 1", "Menejer 2", "Menejer 1", "Menejer 2"]


def test_boss_cannot_assign_lead_to_himself(client, iso):
    resp = client.post("/api/leads", headers=iso["boss"],
                       json={"name": "X", "phone": "+998931112233", "manager": "Bosh Menejer"})
    assert resp.status_code == 400
    lead = client.post("/api/leads", headers=iso["boss"],
                       json={"name": "Y", "phone": "+998931112244", "manager": "Menejer 2"}).json()
    assert lead["manager"] == "Menejer 2"                      # hodimni qo'lda tanlash ishlaydi
    upd = client.put(f"/api/leads/{lead['id']}", headers=iso["boss"], json={**lead, "manager": "Bosh Menejer"})
    assert upd.status_code == 400
    assert storage.get_one("leads", lead["id"])["manager"] == "Menejer 2"
    ok = client.put(f"/api/leads/{lead['id']}", headers=iso["boss"], json={**lead, "manager": "Menejer 1"})
    assert ok.status_code == 200 and ok.json()["manager"] == "Menejer 1"


def test_old_boss_lead_can_still_be_edited(client, iso):
    # Avval Bosh menejerga tushgan lead — boshqa maydonlarini tahrirlash bloklanmaydi
    _seed_leads(1)
    lead = storage.get_one("leads", 1)
    resp = client.put("/api/leads/1", headers=iso["boss"], json={**lead, "amount": 999})
    assert resp.status_code == 200 and resp.json()["amount"] == 999


def test_employee_lead_stays_with_employee(client, iso):
    lead = client.post("/api/leads", headers=iso["m1"], json={"name": "M", "phone": "+998934445566"}).json()
    assert lead["manager"] == "Menejer 1"
    assert any(l["id"] == lead["id"] for l in client.get("/api/leads", headers=iso["boss"]).json())


def test_no_active_employee_leaves_lead_unassigned(client, iso):
    storage.write("users", [u for u in storage.read("users") if u["role"] != "admin"])
    lead = client.post("/api/leads", headers=iso["boss"], json={"name": "Z", "phone": "+998936667788"}).json()
    assert lead["manager"] == ""
    sheet = client.post("/api/integrations/sheets/lead", headers=SHEETS,
                        json={"name": "S", "phone": "+998937778899"}).json()["lead"]
    assert sheet["manager"] == ""