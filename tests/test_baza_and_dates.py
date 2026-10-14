"""BAZA bosqichi va lead kelish sanasi (arrivedAt) bo'yicha filtr testlari."""
from __future__ import annotations

import uuid

import pytest

from app import config, seed, storage
from app.services import arrival

from .conftest import login

SHEETS_URL = "/api/integrations/sheets/lead"


@pytest.fixture(autouse=True)
def _cleanup():
    """Testda yaratilgan lead/savdo/mijozlar o'chiriladi — boshqa test fayllariga ta'sir qilmasin."""
    before = {name: {int(r["id"]) for r in storage.read(name)} for name in ("leads", "sales", "clients")}
    yield
    for name, ids in before.items():
        storage.write(name, [r for r in storage.read(name) if int(r["id"]) in ids])


@pytest.fixture()
def boss(client):
    return login(client, "boss", "boss12345")


@pytest.fixture()
def secret():
    return {"X-Sheets-Secret": config.SHEETS_WEBHOOK_SECRET}


def _uniq_phone() -> str:
    return "+99893" + str(uuid.uuid4().int)[:7]


# ——— 1. Haqiqiy kelish vaqti (target integratsiyasi) ———


def test_target_created_time_is_saved_as_real_arrival(client, secret):
    lead = client.post(SHEETS_URL, headers=secret, json={
        "name": "Target Ali", "phone": _uniq_phone(), "platform": "fb",
        "createdTime": "2026-10-10T21:30:00-05:00", "externalId": f"l:{uuid.uuid4().hex}",
    }).json()["lead"]
    # -05:00 dagi 10-oktabr 21:30 = Toshkentda 11-oktabr 07:30
    assert lead["arrivedAt"] == "2026-10-11T07:30:00+05:00"
    assert lead["arrivedAtSource"] == "target"
    assert lead["targetCreatedAt"] == "2026-10-10T21:30:00-05:00"   # asl qiymat saqlanadi
    assert (lead["date"], lead["time"]) == ("11.10.2026", "07:30")


def test_target_without_created_time_uses_receive_time(client, secret):
    lead = client.post(SHEETS_URL, headers=secret, json={"name": "Vaqtsiz", "phone": _uniq_phone()}).json()["lead"]
    assert lead["arrivedAtSource"] == "webhook"
    assert lead["arrivedAt"].endswith("+05:00")
    assert lead["date"] == ""          # sana taxmin qilinmaydi (avvalgidek)


def test_resent_row_refines_imprecise_arrival(client, secret):
    phone = _uniq_phone()
    first = client.post(SHEETS_URL, headers=secret, json={"name": "Keyin", "phone": phone}).json()["lead"]
    assert first["arrivedAtSource"] == "webhook"
    again = client.post(SHEETS_URL, headers=secret, json={
        "name": "Keyin", "phone": phone, "createdTime": "2026-10-12 09:15"}).json()
    assert again["duplicate"] is True
    assert again["lead"]["arrivedAt"] == "2026-10-12T09:15:00+05:00"
    assert again["lead"]["arrivedAtSource"] == "target"
    # Aniq vaqt boshqa qiymat bilan qayta almashtirilmaydi
    third = client.post(SHEETS_URL, headers=secret, json={
        "name": "Keyin", "phone": phone, "createdTime": "2026-01-01 00:00"}).json()
    assert third["lead"]["arrivedAt"] == "2026-10-12T09:15:00+05:00"


def test_manual_and_website_leads_get_arrival(client, boss):
    lead = client.post("/api/leads", headers=boss, json={"name": "Qo'lda", "phone": _uniq_phone()}).json()
    assert lead["arrivedAtSource"] == "crm" and lead["arrivedAt"].endswith("+05:00")
    # Tahrirlashda (forma butun obyektni yuboradi) arrivedAt o'zgarmaydi
    edited = client.put(f"/api/leads/{lead['id']}", headers=boss,
                        json={**lead, "arrivedAt": "2000-01-01T00:00:00+05:00", "date": "01.01.2000"}).json()
    assert edited["arrivedAt"] == lead["arrivedAt"]
    site = client.post("/api/integrations/website/lead",
                       headers={"X-Website-Secret": config.WEBSITE_WEBHOOK_SECRET},
                       json={"name": "Sayt", "phone": _uniq_phone()}).json()["lead"]
    assert site["arrivedAtSource"] == "website"


def test_backfill_for_legacy_leads():
    legacy = storage.insert("leads", {"name": "Eski", "phone": _uniq_phone(), "stage": "Yangi",
                                      "date": "09.10.2026", "time": "18:45"})
    no_date = storage.insert("leads", {"name": "Eski2", "phone": _uniq_phone(), "stage": "Yangi",
                                       "createdAt": "2026-10-09T20:00:00+00:00"})
    assert seed.backfill_lead_arrivals() >= 2
    assert storage.get_one("leads", legacy["id"])["arrivedAt"] == "2026-10-09T18:45:00+05:00"
    assert storage.get_one("leads", legacy["id"])["arrivedAtSource"] == "legacy"
    # createdAt UTC 20:00 → Toshkent 10-oktabr 01:00
    assert storage.get_one("leads", no_date["id"])["arrivedAt"] == "2026-10-10T01:00:00+05:00"
    assert seed.backfill_lead_arrivals() == 0   # ikkinchi marta hech narsa o'zgarmaydi


# ——— 2. Sana oralig'i filtri ———


def test_date_range_filter_is_inclusive_and_uses_real_arrival(client, boss, secret):
    tag = uuid.uuid4().hex[:6]
    times = {
        "before": "2026-10-09T23:59:00+05:00",
        "start": "2026-10-10T00:00:00+05:00",
        "middle": "2026-10-12T12:00:00+05:00",
        # -05:00 dagi 13-oktabr 13:59 = Toshkentda 13-oktabr 23:59 → kiradi
        "end": "2026-10-13T13:59:00-05:00",
        # -05:00 dagi 13-oktabr 14:00 = Toshkentda 14-oktabr 00:00 → kirmaydi
        "after": "2026-10-13T14:00:00-05:00",
    }
    for label, ts in times.items():
        client.post(SHEETS_URL, headers=secret, json={
            "name": f"{tag}-{label}", "phone": _uniq_phone(), "createdTime": ts})

    def names(params):
        resp = client.get("/api/leads", headers=boss, params=params)
        assert resp.status_code == 200, resp.text
        return {l["name"].split("-", 1)[1] for l in resp.json() if l["name"].startswith(tag)}

    assert names({"dateFrom": "2026-10-10", "dateTo": "2026-10-13"}) == {"start", "middle", "end"}
    assert names({"dateFrom": "10.10.2026", "dateTo": "13.10.2026"}) == {"start", "middle", "end"}
    assert names({"dateFrom": "2026-10-13"}) == {"end", "after"}
    assert names({"dateTo": "2026-10-09"}) == {"before"}
    assert len(names({})) == 5                                  # filtrsiz — hammasi

    assert client.get("/api/leads", headers=boss, params={"dateFrom": "13/10/2026"}).status_code == 400
    assert client.get("/api/leads", headers=boss,
                      params={"dateFrom": "2026-10-13", "dateTo": "2026-10-10"}).status_code == 400


def test_arrival_helpers():
    assert arrival.parse_day("10.10.2026", "x").isoformat() == "2026-10-10"
    assert arrival.arrival_day({"date": "13.10.2026", "time": ""}) == "2026-10-13"
    assert arrival.arrival_day({}) == ""
    assert not arrival.in_range({}, arrival.parse_day("2026-10-10", "x"), None)


# ——— 3. BAZA — CRM ichidagi oddiy bosqich ———


def test_baza_is_regular_stage_left_of_unqualified(client, boss):
    stages = client.get("/api/leads/stages", headers=boss).json()
    assert stages.index("Baza") == stages.index("Sifatsiz lead") - 1   # Sifatsiz lead'ning chap tomonida
    lead = client.post("/api/leads", headers=boss, json={"name": "Bazaga", "phone": _uniq_phone()}).json()
    moved = client.patch(f"/api/leads/{lead['id']}/stage", headers=boss, json={"stage": "Baza"})
    assert moved.status_code == 200 and moved.json()["stage"] == "Baza"
    assert moved.json()["arrivedAt"] == lead["arrivedAt"]        # bosqich o'zgarsa kelgan sana o'zgarmaydi
    back = client.patch(f"/api/leads/{lead['id']}/stage", headers=boss, json={"stage": "Bog'lanildi"})
    assert back.json()["stage"] == "Bog'lanildi"
    # Baza yopilgan bitim emas — savdo yozuvi yaratilmaydi
    assert not [s for s in storage.read("sales") if int(s.get("leadId", 0)) == lead["id"]]


def test_no_postgres_endpoint(client, boss):
    assert client.get("/api/leads/baza", headers=boss).status_code in (404, 405)
