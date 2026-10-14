"""BAZA ustuni (PostgreSQL) va lead kelish sanasi (arrivedAt) bo'yicha filtr testlari.

PostgreSQL testlari uchun haqiqiy server kerak: ``TEST_DATABASE_URL``
(standart: lokal ``sadaf_test`` bazasi). Ulanib bo'lmasa — o'tkazib yuboriladi.
"""
from __future__ import annotations

import os
import uuid

import pytest

from app import config, seed, storage
from app.services import arrival, baza

from .conftest import login

SHEETS_URL = "/api/integrations/sheets/lead"
TEST_DB = os.getenv("TEST_DATABASE_URL", "postgresql://postgres@localhost:5432/sadaf_test")


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
    assert baza.phone_key("+998 90 123-45-67") == baza.phone_key("901234567") == "901234567"


# ——— 3. BAZA (PostgreSQL) ———


def test_baza_disabled_returns_empty_column(client, boss, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", "")
    body = client.get("/api/leads/baza", headers=boss).json()
    assert body["enabled"] is False and body["items"] == []
    assert client.post("/api/leads/baza/1/import", headers=boss, json={"stage": "Yangi"}).status_code == 503


def test_baza_requires_auth(client):
    assert client.get("/api/leads/baza").status_code == 401


@pytest.fixture()
def pg(monkeypatch):
    psycopg = pytest.importorskip("psycopg")
    try:
        conn = psycopg.connect(TEST_DB, autocommit=True, connect_timeout=3)
    except psycopg.Error as exc:
        pytest.skip(f"PostgreSQL mavjud emas: {exc}")
    table = f"baza_{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(config, "DATABASE_URL", TEST_DB)
    monkeypatch.setattr(config, "BAZA_TABLE", table)
    baza.reset_cache()
    yield conn, table
    conn.execute(f'DROP TABLE IF EXISTS "{table}"')
    conn.close()
    baza.reset_cache()


def test_baza_unreachable_database_gives_503(client, boss, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://nobody@127.0.0.1:1/none")
    monkeypatch.setattr(config, "BAZA_CONNECT_TIMEOUT", 1)
    baza.reset_cache()
    resp = client.get("/api/leads/baza", headers=boss)
    assert resp.status_code == 503 and "PostgreSQL" in resp.json()["detail"]
    # Asosiy Kanban baribir ishlaydi
    assert client.get("/api/leads", headers=boss).status_code == 200


def test_baza_auto_creates_table_lists_dedups_and_imports(client, boss, pg):
    conn, table = pg
    assert client.get("/api/leads/baza", headers=boss).json() == {
        "enabled": True, "items": [], "count": 0, "truncated": False, "table": table}  # jadval yaratildi

    crm_phone = _uniq_phone()
    client.post("/api/leads", headers=boss, json={"name": "CRMda bor", "phone": crm_phone})
    p1, p2 = _uniq_phone(), _uniq_phone()
    conn.execute(
        f"""INSERT INTO "{table}" (name, phone, source, created_at) VALUES
            ('Bir', %s, 'Instagram', '2026-10-10 10:00+05'),
            ('Ikki', %s, '', '2026-10-12 23:30+05'),
            ('CRM dublikat', %s, '', '2026-10-11 09:00+05'),
            ('Telefonsiz', '', '', '2026-10-13 08:00+05')""",
        (p1, p2, crm_phone.replace("+", "")),
    )
    # Avtomatik jadvalda telefon UNIQUE: boshqa formatdagi bir xil raqam yozilmaydi
    with pytest.raises(Exception):
        conn.execute(f"""INSERT INTO "{table}" (name, phone) VALUES ('Takror', %s)""", (p1[4:],))

    body = client.get("/api/leads/baza", headers=boss).json()
    names = [i["name"] for i in body["items"]]
    assert names == ["Telefonsiz", "Ikki", "Bir"]           # eng yangisi tepada, CRM'dagisi yo'q
    item = next(i for i in body["items"] if i["name"] == "Bir")
    assert item["arrivedAt"] == "2026-10-10T10:00:00+05:00" and item["date"] == "10.10.2026"
    assert item["source"] == "Instagram"

    rng = client.get("/api/leads/baza", headers=boss, params={"dateFrom": "2026-10-11", "dateTo": "2026-10-12"})
    assert [i["name"] for i in rng.json()["items"]] == ["Ikki"]

    # Kanbanda "Bog'lanildi" ustuniga sudrab o'tkazish → CRM lead
    created = client.post(f"/api/leads/baza/{item['id']}/import", headers=boss, json={"stage": "Bog'lanildi"})
    assert created.status_code == 201, created.text
    lead = created.json()
    assert (lead["name"], lead["stage"], lead["source"]) == ("Bir", "Bog'lanildi", "Instagram")
    assert lead["bazaId"] == item["id"] and lead["arrivedAtSource"] == "baza"
    assert lead["arrivedAt"] == "2026-10-10T10:00:00+05:00"
    # Endi BAZA ustunida ko'rinmaydi, PostgreSQL'da belgilangan, qayta import — 409
    assert "Bir" not in [i["name"] for i in client.get("/api/leads/baza", headers=boss).json()["items"]]
    assert conn.execute(f'SELECT crm_lead_id FROM "{table}" WHERE id = %s', (int(item["id"]),)).fetchone()[0] == lead["id"]
    assert client.post(f"/api/leads/baza/{item['id']}/import", headers=boss, json={"stage": "Yangi"}).status_code == 409
    assert client.post("/api/leads/baza/999999/import", headers=boss, json={"stage": "Yangi"}).status_code == 404
    assert client.post(f"/api/leads/baza/{item['id']}/import", headers=boss, json={"stage": "BAZA"}).status_code == 400
    # Sana filtri bo'yicha CRM'da ham BAZA'dagi haqiqiy kun bilan topiladi
    found = client.get("/api/leads", headers=boss, params={"dateFrom": "2026-10-10", "dateTo": "2026-10-10"}).json()
    assert lead["id"] in [l["id"] for l in found]


def test_baza_existing_custom_table_with_duplicates(client, boss, pg):
    """O'zingizning jadvalingiz: boshqa ustun nomlari, zonasiz vaqt, UNIQUE indeks yo'q."""
    conn, table = pg
    conn.execute(f'''CREATE TABLE "{table}" (id SERIAL PRIMARY KEY, full_name TEXT, telefon TEXT,
                     manba TEXT, created_time TIMESTAMP)''')
    p = _uniq_phone()
    digits = p.replace("+", "")
    conn.execute(
        f"""INSERT INTO "{table}" (full_name, telefon, manba, created_time) VALUES
            ('Asl', %s, 'Facebook', '2026-10-10 09:00'),
            ('Takror 1', %s, 'Facebook', '2026-10-11 09:00'),
            ('Takror 2', %s, 'Facebook', '2026-10-12 09:00'),
            ('Boshqa', %s, '', '2026-10-13 09:00')""",
        (p, digits, digits[3:], _uniq_phone()),
    )
    items = client.get("/api/leads/baza", headers=boss).json()["items"]
    assert sorted(i["name"] for i in items) == ["Asl", "Boshqa"]   # bir raqam — bitta (birinchi kelgani)
    asl = next(i for i in items if i["name"] == "Asl")
    assert asl["arrivedAt"] == "2026-10-10T09:00:00+05:00" and asl["source"] == "Facebook"
    # crm_lead_id ustuni yo'q — import baribir ishlaydi, takror CRM tomonda bloklanadi
    assert client.post(f"/api/leads/baza/{asl['id']}/import", headers=boss, json={"stage": "Yangi"}).status_code == 201
    assert [i["name"] for i in client.get("/api/leads/baza", headers=boss).json()["items"]] == ["Boshqa"]


def test_baza_text_date_column_filtered_in_python(client, boss, pg):
    conn, table = pg
    conn.execute(f'CREATE TABLE "{table}" (id SERIAL PRIMARY KEY, name TEXT, phone TEXT, sana TEXT)')
    conn.execute(f"""INSERT INTO "{table}" (name, phone, sana) VALUES
        ('A', %s, '09.10.2026 10:00'), ('B', %s, '11.10.2026'), ('C', %s, 'noma''lum')""",
                 (_uniq_phone(), _uniq_phone(), _uniq_phone()))
    rng = client.get("/api/leads/baza", headers=boss, params={"dateFrom": "2026-10-10", "dateTo": "2026-10-13"})
    assert [i["name"] for i in rng.json()["items"]] == ["B"]
    assert len(client.get("/api/leads/baza", headers=boss).json()["items"]) == 3
