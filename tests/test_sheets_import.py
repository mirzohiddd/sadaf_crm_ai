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


def test_lead_created_without_name_and_phone(client, secret):
    """Ism/telefon majburiy emas — bo'sh qoladi, lead baribir yaratiladi."""
    no_name = _post(client, secret, phone="+998900000006", platform="ig", externalId="l:9006")
    assert no_name["duplicate"] is False and no_name["lead"]["name"] == ""
    assert no_name["lead"]["phone"] == "+998900000006"

    no_phone = _post(client, secret, name="Ism", phone=None, comment="telefonsiz", externalId="l:9007")["lead"]
    assert no_phone["phone"] == "" and no_phone["name"] == "Ism" and no_phone["comment"] == "telefonsiz"

    nothing = _post(client, secret, createdTime="2026-08-06T07:39:23-05:00")["lead"]
    assert (nothing["name"], nothing["phone"], nothing["source"], nothing["stage"]) == ("", "", "", "Yangi")
    assert nothing["date"] == "06.08.2026"
    assert nothing["people"] is None and nothing["amount"] is None and nothing["tour"] == ""
    assert nothing["id"] in [l["id"] for l in storage.read("leads")]


def test_leads_without_phone_are_not_duplicates_of_each_other(client, secret):
    a = _post(client, secret, name="A", externalId="l:9008")
    b = _post(client, secret, name="B", externalId="l:9009")
    c = _post(client, secret, name="C")  # na id, na telefon
    assert not a["duplicate"] and not b["duplicate"] and not c["duplicate"]
    assert len({a["lead"]["id"], b["lead"]["id"], c["lead"]["id"]}) == 3


def test_id_duplicate_still_detected_without_phone(client, secret):
    first = _post(client, secret, name="Birinchi", externalId="l:9010")
    again = _post(client, secret, name="", externalId="l:9010")
    assert again["duplicate"] is True and again["lead"]["id"] == first["lead"]["id"]


def test_website_endpoint_still_requires_name_and_phone(client, monkeypatch):
    """Qoida faqat Google Sheets uchun — sayt arizasi o'zgarmagan."""
    monkeypatch.setattr("app.routers.integrations.WEBSITE_WEBHOOK_SECRET", "web-test-secret")
    resp = client.post("/api/integrations/website/lead", headers={"X-Website-Secret": "web-test-secret"},
                       json={"name": "", "phone": "+998900000011"})
    assert resp.status_code == 400


def test_wrong_secret_rejected(client, secret):
    resp = client.post(URL, headers={"X-Sheets-Secret": "wrong"}, json={"name": "X", "phone": "+998900000007"})
    assert resp.status_code == 401


def test_imported_lead_without_manager_visible_to_boss(client, secret):
    lead = _post(client, secret, name="Biriktirilmagan", phone="+998900000008")["lead"]
    boss = login(client, "boss", "boss12345")
    ids = [l["id"] for l in client.get("/api/leads", headers=boss).json()]
    assert lead["id"] in ids
    assert config.SHEETS_WEBHOOK_SECRET  # mavjud sozlama o'zgarmagan


# ——— Dublikat topilganda mavjud leadning BO'SH maydonlarini to'ldirish ———


def _lead(lead_id):
    return next(l for l in storage.read("leads") if int(l["id"]) == int(lead_id))


def test_duplicate_by_phone_fills_empty_name_rustamjon(client, secret, monkeypatch):
    """CRM: name="" phone="+998901687722"; Sheets: Rustamjon | 998901687722 → name yoziladi."""
    old = _post(client, secret, phone="+998901687722")["lead"]
    assert old["name"] == ""
    count = len(storage.read("leads"))
    monkeypatch.setattr("app.storage.now_iso", lambda: "2099-01-01T00:00:00+00:00")

    resp = _post(client, secret, name="Rustamjon", phone="998901687722", platform="ig",
                 comment="Sheets izohi", createdTime="2026-08-06T07:39:23-05:00")
    lead = resp["lead"]
    assert resp["duplicate"] is True                       # javob formati saqlangan
    assert lead["id"] == old["id"]                         # yangi lead yaratilmadi, ID o'zgarmadi
    assert len(storage.read("leads")) == count
    assert lead["name"] == "Rustamjon"
    assert lead["phone"] == "+998901687722"                # mavjud telefon almashtirilmadi
    assert (lead["source"], lead["comment"]) == ("Instagram", "Sheets izohi")
    assert (lead["date"], lead["time"]) == ("06.08.2026", "07:39")
    assert lead["createdAt"] == old["createdAt"]           # createdAt o'zgarmaydi
    assert lead["updatedAt"] == "2099-01-01T00:00:00+00:00"  # updatedAt yangilandi
    assert set(resp["updated"]) >= {"name", "source", "comment", "date", "time"}
    assert _lead(old["id"])["name"] == "Rustamjon"         # bazada ham saqlangan


def test_duplicate_never_overwrites_existing_values(client, secret, monkeypatch):
    """CRM'da ism "Rustamjon" bo'lsa, Sheets'dan boshqa ism kelsa ham o'zgarmaydi."""
    old = _post(client, secret, name="Rustamjon", phone="+998901687733", tour="Dubay", comment="eski",
                platform="Telegram", leadStatus="Bog'lanildi", createdTime="2026-08-01T10:00:00+05:00")["lead"]
    monkeypatch.setattr("app.storage.now_iso", lambda: "2099-01-01T00:00:00+00:00")

    resp = _post(client, secret, name="Boshqa Ism", phone="998901687733", tour="Turkiya", comment="yangi",
                 platform="ig", leadStatus="Yangi", createdTime="2026-09-09T09:09:09+05:00",
                 city="Samarqand", amount=1500, people=2)
    lead = resp["lead"]
    assert resp["duplicate"] is True and lead["id"] == old["id"]
    # To'ldirilgan maydonlar o'zgarmadi
    assert (lead["name"], lead["tour"], lead["comment"]) == ("Rustamjon", "Dubay", "eski")
    assert (lead["source"], lead["stage"], lead["date"]) == ("Telegram", "Bog'lanildi", "01.08.2026")
    # Faqat bo'sh maydonlar to'ldirildi
    assert (lead["city"], lead["amount"], lead["people"]) == ("Samarqand", 1500.0, 2)
    assert sorted(resp["updated"]) == ["amount", "city", "people"]


def test_duplicate_with_nothing_new_changes_nothing(client, secret, monkeypatch):
    old = _post(client, secret, name="Rustamjon", phone="+998901687744")["lead"]
    monkeypatch.setattr("app.storage.now_iso", lambda: "2099-01-01T00:00:00+00:00")
    resp = _post(client, secret, name="", phone="998901687744")  # bo'sh qiymatlar hech narsani o'chirmaydi
    assert resp["duplicate"] is True and resp["updated"] == []
    assert resp["lead"]["name"] == "Rustamjon"
    assert _lead(old["id"])["updatedAt"] == old["updatedAt"]   # o'zgarish bo'lmasa updatedAt ham o'zgarmaydi


def test_duplicate_by_id_fills_empty_phone(client, secret):
    old = _post(client, secret, name="Telefonsiz", externalId="l:9020")["lead"]
    assert old["phone"] == ""
    resp = _post(client, secret, name="Telefonsiz", phone="+998901687755", externalId="l:9020")
    assert resp["duplicate"] is True and resp["lead"]["id"] == old["id"]
    assert resp["lead"]["phone"] == "+998901687755" and resp["updated"] == ["phone"]


def test_duplicate_fills_crm_lead_with_zero_amount(client, secret):
    """CRM formasi bo'sh summa/odam sonini 0 deb saqlaydi — Sheets qiymati bilan to'ldiriladi."""
    boss = login(client, "boss", "boss12345")
    crm = client.post("/api/leads", headers=boss, json={"name": "Qo'lda kiritilgan", "phone": "+998901687766",
                                                        "amount": 0, "people": 0, "tour": "Misr"})
    assert crm.status_code == 201, crm.text
    resp = _post(client, secret, name="Boshqa", phone="998901687766", amount="900", people=3, tour="Turkiya")
    lead = resp["lead"]
    assert lead["id"] == crm.json()["id"]
    assert (lead["name"], lead["tour"]) == ("Qo'lda kiritilgan", "Misr")
    assert (lead["amount"], lead["people"]) == (900.0, 3)
    # Odatiy CRM API ham yangilangan leadni ko'radi
    got = client.get(f"/api/leads/{lead['id']}", headers=boss)
    if got.status_code == 200:
        assert got.json()["amount"] == 900.0


def test_matched_by_phone_gets_external_id_for_future_imports(client, secret):
    old = _post(client, secret, name="A", phone="+998901687777")["lead"]
    _post(client, secret, phone="998901687777", externalId="l:9030")
    assert _lead(old["id"])["externalId"] == "l:9030"
    again = _post(client, secret, externalId="l:9030")       # endi id bo'yicha topiladi
    assert again["duplicate"] is True and again["lead"]["id"] == old["id"]