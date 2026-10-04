"""Google Sheets → Lead importi: maydonlar moslamasi, bo'sh qiymatlar, dublikatlar."""
from __future__ import annotations

import pytest

from app import config, storage

from .conftest import login

URL = "/api/integrations/sheets/lead"


@pytest.fixture()
def secret(monkeypatch):
    monkeypatch.setattr("app.routers.integrations.SHEETS_WEBHOOK_SECRET", "sheet-test-secret")
    return {"X-Sheets-Secret": "sheet-test-secret"}


@pytest.fixture(autouse=True)
def cleanup(client):
    before = {int(l["id"]) for l in storage.read("leads")}
    sales_before = {int(s["id"]) for s in storage.read("sales")}
    yield
    storage.write("leads", [l for l in storage.read("leads") if int(l["id"]) in before])
    storage.write("sales", [s for s in storage.read("sales") if int(s["id"]) in sales_before])


def _post(client, secret, **row):
    resp = client.post(URL, headers=secret, json=row)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_meta_row_maps_only_crm_fields_and_leaves_rest_empty(client, secret):
    data = _post(
        client, secret,
        name="Ali Valiyev", phone="p:+998901110001", platform="ig", leadStatus="CREATED",
        comment="Turkiya haqida", createdTime="2026-08-06T07:39:23-05:00", externalId="l:9001",
        rowId=2, sheet="Sheet1", campaign="Kampaniya", ad="Ad X",
    )
    lead = data["lead"]
    assert data["duplicate"] is False
    assert lead["name"] == "Ali Valiyev"
    assert lead["phone"] == "+998901110001"         # "p:" prefiksi olib tashlandi
    assert lead["source"] == "Instagram"             # Manba ← platform
    assert lead["stage"] == "Yangi"                  # CREATED — CRM bosqichi emas
    assert lead["comment"] == "Turkiya haqida"
    assert (lead["date"], lead["time"]) == ("06.08.2026", "07:39")
    # Jadvalda yo'q — bo'sh (taxmin qilinmaydi)
    assert lead["tour"] == "" and lead["people"] is None and lead["amount"] is None
    assert lead["manager"] == "" and lead["telegram"] == "" and lead["city"] == ""
    # Faqat CRM maydonlari — Meta'ning qo'shimcha ustunlari leadga yozilmaydi
    for extra in ("campaign", "ad", "leadStatus", "platform", "formName"):
        assert extra not in lead
    assert lead["externalId"] == "l:9001" and lead["sheetName"] == "Sheet1"


def test_optional_crm_columns_are_used_when_present(client, secret):
    lead = _post(
        client, secret,
        name="Jasur Toshev", phone=998977770002, tour="Dubay", people=3, amount="1 500 $",
        manager="Menejer", platform="Telegram", leadStatus="bog\u2018lanildi", telegram="@jasur",
        city="Samarqand", comment="VIP", createdTime="07.08.2026 14:30", externalId="x-9002", sheet="Sheet3",
    )["lead"]
    assert lead["phone"] == "998977770002"
    assert (lead["tour"], lead["people"], lead["amount"]) == ("Dubay", 3, 1500.0)
    assert lead["manager"] == "Menejer"
    assert lead["source"] == "Telegram"
    assert lead["stage"] == "Bog'lanildi"           # apostrof turi va registr farq qilmaydi
    assert (lead["telegram"], lead["city"]) == ("@jasur", "Samarqand")
    assert (lead["date"], lead["time"]) == ("07.08.2026", "14:30")


def test_unparseable_values_stay_empty(client, secret):
    lead = _post(client, secret, name="Bekzod", phone="+998911110003", people="kelishiladi",
                 amount="kelishiladi", createdTime="")["lead"]
    assert lead["people"] is None and lead["amount"] is None
    assert lead["source"] == "" and lead["stage"] == "Yangi"
    assert lead["date"] == "" and lead["time"] == ""


def test_won_stage_from_sheet_syncs_sale(client, secret):
    lead = _post(client, secret, name="Madina", phone="+998935550004", leadStatus="To'lov qilindi",
                 externalId="l:9004")["lead"]
    assert lead["stage"] == "To'lov qilindi"
    assert any(int(s.get("leadId", 0)) == int(lead["id"]) for s in storage.read("sales"))


def test_duplicates_by_id_and_phone_across_sheets(client, secret):
    first = _post(client, secret, name="Sardor", phone="+998970000005", externalId="l:9005", sheet="Sheet1")
    by_phone = _post(client, secret, name="Sardor (Sheet3)", phone="+998 97 000-00-05", externalId="x-9005", sheet="Sheet3")
    by_id = _post(client, secret, name="Boshqa ism", phone="+998990000099", externalId="l:9005", sheet="Sheet3")
    assert by_phone["duplicate"] is True and by_id["duplicate"] is True
    assert by_phone["lead"]["id"] == by_id["lead"]["id"] == first["lead"]["id"]
    assert sum(1 for l in storage.read("leads") if l.get("externalId") in ("l:9005", "x-9005")) == 1


def test_missing_name_or_phone_rejected(client, secret):
    assert client.post(URL, headers=secret, json={"name": "", "phone": "+998900000006"}).status_code == 400
    assert client.post(URL, headers=secret, json={"name": "Ism", "phone": None}).status_code == 400


def test_wrong_secret_rejected(client, secret):
    resp = client.post(URL, headers={"X-Sheets-Secret": "wrong"}, json={"name": "X", "phone": "+998900000007"})
    assert resp.status_code == 401


def test_imported_lead_without_manager_visible_to_boss(client, secret):
    lead = _post(client, secret, name="Biriktirilmagan", phone="+998900000008")["lead"]
    boss = login(client, "boss", "boss12345")
    ids = [l["id"] for l in client.get("/api/leads", headers=boss).json()]
    assert lead["id"] in ids
    assert config.SHEETS_WEBHOOK_SECRET  # mavjud sozlama o'zgarmagan
