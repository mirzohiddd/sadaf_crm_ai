"""Turlar (Tours) CRUD: nomi, yo'nalish, ketish sanasi, so'mdagi narx."""
from __future__ import annotations

import pytest

from app import storage

from .conftest import login


@pytest.fixture(autouse=True)
def _cleanup():
    before = {int(r["id"]) for r in storage.read("tours")}
    yield
    storage.write("tours", [r for r in storage.read("tours") if int(r["id"]) in before])


@pytest.fixture()
def boss(client):
    return login(client, "boss", "boss12345")


VALID = {"name": "Dubay + Abu-Dabi", "country": "BAA", "departureDate": "2026-11-20",
         "price": 12_500_000, "days": 6, "seats": 10, "status": "Faol"}


def test_create_list_update_delete_persists(client, boss):
    resp = client.post("/api/tours", headers=boss, json=VALID)
    assert resp.status_code == 201, resp.text
    tour = resp.json()
    tid = tour["id"]
    assert tour["price"] == 12_500_000 and tour["currency"] == "UZS"
    assert tour["departureDate"] == "2026-11-20"

    # Diskka yozilgan (qayta o'qilganda ham bor)
    saved = storage.get_one("tours", tid)
    assert saved and saved["name"] == "Dubay + Abu-Dabi" and saved["price"] == 12_500_000

    listed = client.get("/api/tours", headers=boss).json()
    assert any(t["id"] == tid and t["price"] == 12_500_000 for t in listed)

    upd = client.put(f"/api/tours/{tid}", headers=boss,
                     json={**VALID, "name": "Dubay VIP", "price": "15000000", "departureDate": "2026-12-01"})
    assert upd.status_code == 200, upd.text
    assert upd.json()["name"] == "Dubay VIP"
    assert upd.json()["price"] == 15_000_000
    assert storage.get_one("tours", tid)["departureDate"] == "2026-12-01"

    assert client.delete(f"/api/tours/{tid}", headers=boss).status_code == 200
    assert storage.get_one("tours", tid) is None
    assert all(t["id"] != tid for t in client.get("/api/tours", headers=boss).json())


@pytest.mark.parametrize("field,value", [
    ("name", ""), ("country", " "), ("departureDate", ""), ("departureDate", "20.11.2026"),
    ("price", 0), ("price", -5), ("price", "abc"),
])
def test_create_rejects_invalid(client, boss, field, value):
    resp = client.post("/api/tours", headers=boss, json={**VALID, field: value})
    assert resp.status_code == 422, (field, value, resp.text)


def test_create_requires_departure_date(client, boss):
    body = {k: v for k, v in VALID.items() if k != "departureDate"}
    assert client.post("/api/tours", headers=boss, json=body).status_code == 422


def test_end_date_not_before_departure(client, boss):
    resp = client.post("/api/tours", headers=boss, json={**VALID, "endDate": "2026-11-10"})
    assert resp.status_code == 422


def test_partial_update_keeps_other_fields(client, boss):
    tid = client.post("/api/tours", headers=boss, json=VALID).json()["id"]
    resp = client.put(f"/api/tours/{tid}", headers=boss, json={"seats": 3})
    assert resp.status_code == 200
    row = storage.get_one("tours", tid)
    assert row["seats"] == 3 and row["price"] == 12_500_000 and row["departureDate"] == "2026-11-20"
    # Tahrirlashda noto'g'ri narx rad etiladi
    assert client.put(f"/api/tours/{tid}", headers=boss, json={"price": 0}).status_code == 422


def test_legacy_usd_tour_still_listed_and_editable(client, boss):
    legacy = storage.insert("tours", {"name": "Eski tur", "country": "Turkiya", "days": 7,
                                      "price": 900, "seats": 5, "status": "Faol"})
    listed = client.get("/api/tours", headers=boss).json()
    row = next(t for t in listed if t["id"] == legacy["id"])
    assert row["price"] == 900 and "currency" not in row
    # Status o'zgartirish narxga tegmaydi
    assert client.put(f"/api/tours/{legacy['id']}", headers=boss, json={"status": "Yopiq"}).status_code == 200
    assert "currency" not in storage.get_one("tours", legacy["id"])
    # Narx so'mda kiritilsa — tur so'mga o'tadi
    resp = client.put(f"/api/tours/{legacy['id']}", headers=boss,
                      json={"price": 11_000_000, "departureDate": "2027-01-05"})
    assert resp.status_code == 200 and resp.json()["currency"] == "UZS"


def test_tours_excel_columns(client, boss):
    from app.services import excel

    client.post("/api/tours", headers=boss, json=VALID)
    storage.insert("tours", {"name": "Eski USD", "country": "Misr", "price": 700})
    headers, rows = excel.build_sheet_data("tours", {"id": 1, "role": "super_admin"})
    assert "Ketish sanasi" in headers and "Valyuta" in headers
    cur = headers.index("Valyuta")
    by_name = {r[headers.index("Tur nomi")]: r for r in rows}
    assert by_name["Dubay + Abu-Dabi"][cur] == "UZS"
    assert by_name["Eski USD"][cur] == "USD"
