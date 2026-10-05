"""/api/integrations/sheets — Google Sheets → CRM lead integratsiyasi.

Oqim bir tomonlama: Google Sheetsga yangi qator (lead) tushganda, o'sha
jadvalga ulangan Apps Script trigger shu endpointga POST so'rov yuboradi
va CRM avtomatik lead yaratadi. CRM Google Sheetsga hech narsa yozmaydi —
jadvaldagi mavjud ma'lumotlar hech qachon o'zgartirilmaydi yoki o'chirilmaydi.

Bu odatdagi login/JWT bilan himoyalangan endpointlardan farqli — uni
chaqiruvchi (Apps Script) tizimga "hodim" sifatida kirmaydi, shuning uchun
alohida maxfiy kalit (X-Sheets-Secret header) bilan himoyalangan.

Jadval Meta (Facebook/Instagram) Lead Ads eksporti bo'lishi mumkin. Lead
faqat CRM'ning o'z maydonlariga tushadi (ism, telefon, tur, odam soni, summa,
mas'ul menejer, manba, bosqich, telegram, shahar, izoh, sana). Jadvalda
bo'lmagan ma'lumot BO'SH qoldiriladi — hech narsa taxmin qilinmaydi.

Google Sheets tomonidagi sozlash (Apps Script namunasi) uchun:
    backend/scripts/google-sheets-webhook.gs
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Header, HTTPException, status

from .. import storage
from ..config import (
    SHEETS_WEBHOOK_SECRET,
    WEBSITE_DEFAULT_SOURCE,
    WEBSITE_WEBHOOK_SECRET,
)
from ..schemas import SheetLeadIn, WebsiteLeadIn
from ..services import assignment, notify
from ..services.crm import norm
from . import leads as leads_router  # _sync_sale / _sync_client qayta ishlatish uchun

router = APIRouter(prefix="/api/integrations", tags=["integrations"])

# Meta jadvalidagi "platform" ustuni qisqartmalarini CRM'dagi Manba (source)
# nomlariga moslashtiradi — SourceTag.vue shu nomlarni tanib rangli belgi chizadi.
PLATFORM_LABELS = {
    "ig": "Instagram",
    "instagram": "Instagram",
    "fb": "Facebook",
    "facebook": "Facebook",
}


def _digits(phone: Any) -> str:
    # Meta eksporti "p:+998..." ko'rinishida beradi — prefiks raqam emas, tashlanadi.
    return re.sub(r"\D", "", re.sub(r"^\s*p:", "", str(phone or ""), flags=re.I))


def _find_lead_by_phone(phone: Any) -> dict[str, Any] | None:
    """Telefon raqami bo'yicha mavjud leadni topadi (format farqidan qat'i nazar)."""
    target = _digits(phone)
    if not target:
        return None
    for lead in storage.read("leads"):
        if _digits(lead.get("phone")) == target:
            return lead
    return None


def _find_lead_by_external_id(external_id: Any) -> dict[str, Any] | None:
    """Meta lead ID'si bo'yicha mavjud leadni topadi (eng ishonchli dublikat tekshiruvi)."""
    if not external_id:
        return None
    for lead in storage.read("leads"):
        if str(lead.get("externalId") or "") == str(external_id):
            return lead
    return None


# created_time ISO bo'lmasa (jadval sanani o'zi formatlagan bo'lsa) sinab ko'riladigan formatlar.
_SHEET_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
    "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y",
    "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y",
)


def _split_created_time(created_time: str) -> tuple[str, str]:
    """created_time ("2026-08-06T07:39:23-05:00") ni CRM formatiga o'giradi:
    sana — DD.MM.YYYY, vaqt — HH:MM (vaqt ko'rsatilmagan bo'lsa — bo'sh).

    created_time bo'sh yoki o'qib bo'lmaydigan bo'lsa — sana/vaqt BO'SH qoladi
    (taxmin qilinmaydi; importning o'z vaqti ``createdAt`` da saqlanadi).
    """
    text = (created_time or "").strip()
    if not text:
        return "", ""
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt.strftime("%d.%m.%Y"), dt.strftime("%H:%M")
    except ValueError:
        pass
    for fmt in _SHEET_DATE_FORMATS:
        try:
            dt = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return dt.strftime("%d.%m.%Y"), (dt.strftime("%H:%M") if "%H" in fmt else "")
    return "", ""


def _sheet_stage(lead_status: str) -> str:
    """lead_status CRM bosqichlaridan biriga mos kelsa — o'sha bosqich, aks holda "Yangi".

    Meta'ning o'z holatlari (masalan "CREATED") CRM bosqichi emas — ular
    "Yangi" deb olinadi. Katta-kichik harf va apostrof turlari farq qilmaydi.
    """
    wanted = norm(lead_status)
    if wanted:
        for stage in leads_router.STAGES:
            if norm(stage) == wanted:
                return stage
    return leads_router.STAGES[0]  # "Yangi"


def _sheet_source(platform: str, source: str) -> str:
    """Manba = platform ustuni ("ig" → Instagram, "fb" → Facebook). Bo'sh bo'lsa — bo'sh."""
    platform = platform.strip()
    if platform:
        return PLATFORM_LABELS.get(platform.lower(), platform)
    return source.strip()


# Dublikat topilganda Sheets qiymati bilan to'ldiriladigan maydonlar (faqat CRM'da bo'sh bo'lsa).
_SHEET_FILLABLE = (
    "name", "phone", "tour", "people", "amount", "manager", "source",
    "stage", "telegram", "city", "comment", "date", "time",
)
# CRM formasi bo'sh qoldirilgan odam soni/summani 0 deb saqlaydi — 0 ham "bo'sh" hisoblanadi.
_ZERO_IS_BLANK = ("people", "amount")


def _is_blank(field: str, value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if field in _ZERO_IS_BLANK and isinstance(value, (int, float)) and not isinstance(value, bool):
        return value == 0
    return False


def _fill_empty_lead_fields(lead_id: int, incoming: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    """Mavjud leadning FAQAT bo'sh maydonlarini Sheets qiymati bilan to'ldiradi.

    - To'ldirilgan (bo'sh bo'lmagan) qiymat hech qachon ustidan yozilmaydi.
    - Sheets'da bo'sh kelgan qiymat hech narsani o'zgartirmaydi.
    - ``id`` va ``createdAt`` o'zgarmaydi; biror maydon to'ldirilsa ``updatedAt`` yangilanadi.

    Tekshiruv va yozish bitta ``storage.mutate`` ichida — parallel so'rovlarda ham xavfsiz.
    Qaytaradi: (lead, to'ldirilgan maydonlar ro'yxati).
    """
    filled: list[str] = []

    def _needs_fill(row: dict[str, Any]) -> bool:
        if incoming.get("externalId") and not str(row.get("externalId") or "").strip():
            return True
        return any(
            not _is_blank(f, incoming.get(f)) and _is_blank(f, row.get(f)) for f in _SHEET_FILLABLE
        )

    current = next((r for r in storage.read("leads") if int(r.get("id", 0)) == int(lead_id)), None)
    if current is None or not _needs_fill(current):
        # To'ldiradigan narsa yo'q — fayl qayta yozilmaydi, updatedAt o'zgarmaydi.
        return current, filled

    def _do(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        for row in rows:
            if int(row.get("id", 0)) != int(lead_id):
                continue
            for field in _SHEET_FILLABLE:
                value = incoming.get(field)
                if not _is_blank(field, value) and _is_blank(field, row.get(field)):
                    row[field] = value
                    filled.append(field)
            # Keyingi importlarda id bo'yicha topilishi uchun (texnik maydon, ekranda ko'rinmaydi).
            if incoming.get("externalId") and not str(row.get("externalId") or "").strip():
                row["externalId"] = incoming["externalId"]
                filled.append("externalId")
            if filled:
                row["updatedAt"] = storage.now_iso()
            return dict(row)
        return None

    lead = storage.mutate("leads", _do)
    return lead, filled


def _check_secret(x_sheets_secret: str | None) -> None:
    if not x_sheets_secret or x_sheets_secret != SHEETS_WEBHOOK_SECRET:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Noto'g'ri yoki yo'q maxfiy kalit.")


@router.post("/sheets/lead", status_code=status.HTTP_201_CREATED)
def create_lead_from_sheet(
    body: SheetLeadIn,
    x_sheets_secret: str | None = Header(default=None, alias="X-Sheets-Secret"),
) -> dict[str, Any]:
    """Google Sheetsdan (Sheet1 / Sheet3) kelgan bitta qatorni CRM lead sifatida yaratadi.

    Maydonlar moslamasi (jadvalda bo'lmasa — BO'SH, hech narsa taxmin qilinmaydi):

    ===================  ==========================================
    CRM                  Google Sheets ustuni
    ===================  ==========================================
    Ism Familiya         ismingiz
    Telefon raqam        phone_number, bo'lmasa telefon_raqamingiz
    Qaysi tur            tur (mavjud bo'lsa)
    Nechta odam          odam soni (mavjud bo'lsa)
    Summa (USD)          summa (mavjud bo'lsa)
    Mas'ul menejer       menejer (mavjud bo'lsa)
    Manba                platform ("ig" → Instagram, "fb" → Facebook)
    Bosqich              lead_status (CRM bosqichi bo'lmasa — "Yangi")
    Telegram username    telegram (mavjud bo'lsa)
    Shahar               shahar (mavjud bo'lsa)
    Kommentariya         Comment
    Sana                 created_time
    ===================  ==========================================

    - Duplikat: avval jadvaldagi ``id`` (``externalId``), keyin telefon
      raqami bo'yicha — mos lead bo'lsa yangisi yaratilmaydi (``duplicate: true``).
      Sheet1 va Sheet3 orasida ham. Bunda mavjud leadning FAQAT BO'SH
      maydonlari Sheets qiymati bilan to'ldiriladi (``updated`` — to'ldirilgan
      maydonlar ro'yxati); to'ldirilgan qiymatlar hech qachon almashtirilmaydi.
    - Mas'ul menejer jadvalda bo'lmasa — lead biriktirilmagan holda qoladi
      (uni Super Admin ko'radi va CRM'dan biriktiradi).
    - Ism va telefon majburiy EMAS: bo'lmasa "" saqlanadi va lead baribir yaratiladi
      (bu qoida faqat shu endpoint uchun; sayt arizasida ism/telefon majburiyligicha qoladi).
    """
    _check_secret(x_sheets_secret)

    # Ism va telefon majburiy emas — jadvalda bo'lmasa bo'sh qoladi, lead baribir yaratiladi.
    name = body.name.strip()
    phone = re.sub(r"^\s*p:", "", body.phone.strip(), flags=re.I).strip()

    # Dublikat: avval jadvaldagi id, keyin (telefon bo'lsa) telefon bo'yicha.
    # Telefon bo'sh bo'lsa telefon bo'yicha tekshirilmaydi (_find_lead_by_phone bo'sh raqamni o'tkazib yuboradi).
    external_id = body.externalId.strip()
    existing = _find_lead_by_external_id(external_id) or _find_lead_by_phone(phone)
    date, time = _split_created_time(body.createdTime)

    if existing:
        # Yangi lead YARATILMAYDI — mavjud leadning faqat bo'sh maydonlari to'ldiriladi
        # (masalan CRM'da ism bo'sh, Sheets'da "Rustamjon" bo'lsa — ism yoziladi;
        # CRM'da ism allaqachon bor bo'lsa — o'zgarmaydi).
        incoming = {
            "name": name,
            "phone": phone,
            "tour": body.tour,
            "people": body.people,
            "amount": body.amount,
            "manager": body.manager,
            "source": _sheet_source(body.platform, body.source),
            # lead_status bo'lmasa "Yangi" — faqat CRM'dagi bosqich bo'sh bo'lsa ishlatiladi.
            "stage": _sheet_stage(body.leadStatus),
            "telegram": body.telegram,
            "city": body.city,
            "comment": body.comment,
            "date": date,
            "time": time,
            "externalId": external_id,
        }
        lead, filled = _fill_empty_lead_fields(int(existing["id"]), incoming)
        lead = lead or existing
        if filled:
            # Bosqich/summa to'ldirilgan bo'lsa — savdo/mijoz sinxronizatsiyasi odatdagidek.
            leads_router._sync_sale(lead)
            leads_router._sync_client(lead)
            notify.log("Google Sheets", "update", "lead", lead.get("name", ""),
                       {"fields": filled}, actor_id=None)
        return {"ok": True, "duplicate": True, "updated": filled, "lead": lead}

    data: dict[str, Any] = {
        "name": name,
        "phone": phone,
        "tour": body.tour,
        "people": body.people,
        "amount": body.amount,
        "manager": body.manager,
        "source": _sheet_source(body.platform, body.source),
        "stage": _sheet_stage(body.leadStatus),
        "telegram": body.telegram,
        "city": body.city,
        "comment": body.comment,
        "ownerId": None,     # avtomatik yaratilgan — inson egasi yo'q
        "seenBy": [],        # hamma admin uchun "yangi" badge sifatida ko'rinadi
        "date": date,
        "time": time,
    }
    # Texnik maydonlar (ekranda ko'rinmaydi) — faqat dublikatni aniqlash va kuzatuv uchun.
    if external_id:
        data["externalId"] = external_id
    if body.rowId is not None:
        data["sheetRowId"] = body.rowId
    if body.sheet:
        data["sheetName"] = body.sheet

    created = storage.insert("leads", data)
    # lead_status yopilgan bosqich ("To'lov qilindi", "Bron tasdiqlandi") bo'lsa —
    # savdo/mijoz sinxronizatsiyasi qo'lda kiritilgan leaddagidek ishlaydi.
    leads_router._sync_sale(created)
    leads_router._sync_client(created)

    notify.log("Google Sheets", "create", "lead", created.get("name", ""),
               {"amount": created.get("amount", 0)}, actor_id=None)
    notify.broadcast_admins(
        "Yangi lead (Google Sheets)",
        f"{created.get('name')} — {created.get('source')} · {created.get('tour') or '—'}",
        amount=float(created.get("amount") or 0),
        kind="lead",
        link="/ledlar",
    )

    return {"ok": True, "duplicate": False, "lead": created}


def _check_website_secret(x_website_secret: str | None) -> None:
    if not x_website_secret or x_website_secret != WEBSITE_WEBHOOK_SECRET:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Noto'g'ri yoki yo'q maxfiy kalit.")


@router.post("/website/lead", status_code=status.HTTP_201_CREATED)
def create_lead_from_website(
    body: WebsiteLeadIn,
    x_website_secret: str | None = Header(default=None, alias="X-Website-Secret"),
) -> dict[str, Any]:
    """Lead Form (``sadaf-landing``) dan kelgan arizani CRM lead sifatida yaratadi.

    Bu endpoint login qilinmagan tashrifchi tomonidan chaqiriladi (forma
    JWT bilan ishlamaydi), shuning uchun ``/api/leads`` (JWT talab qiladigan
    ichki CRM endpointi) o'rniga alohida, maxfiy kalit (``X-Website-Secret``)
    bilan himoyalangan yo'l ishlatiladi. Mavjud ``/api/leads`` va Google
    Sheets webhooki hech qanday o'zgarishsiz qoladi.

    - Duplikat oldini olish: telefon raqami bo'yicha (formatdan qat'i
      nazar) — agar shu raqamda lead allaqachon bo'lsa, yangisi
      yaratilmaydi, mavjudi ``duplicate: true`` bilan qaytariladi.
    - ``destination`` → mavjud ``tour`` maydoniga yoziladi (Leads jadvalida
      "Tur" ustunida ko'rinadi).
    - ``travelDate`` → alohida saqlanadi va "Izoh" ustunida ham ko'rinadi.
    - Lead darhol ``createdAt``/``date``/``time`` bilan, "Yangi" bosqichda
      va aktiv adminlar orasida navbat bilan (round-robin) biriktirilgan
      holda yaratiladi — CRM ochiq oynalarga WebSocket orqali zudlik bilan
      bildirishnoma boradi (qo'shimcha sozlash shart emas).
    """
    _check_website_secret(x_website_secret)

    name = body.name.strip()
    phone = body.phone.strip()
    if not name or not phone:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Lead uchun ism va telefon shart.")

    existing = _find_lead_by_phone(phone)
    if existing:
        return {"ok": True, "duplicate": True, "lead": existing}

    assigned = assignment.next_admin()
    now_iso = datetime.now().astimezone().isoformat()

    comment_parts = []
    if body.travelDate:
        comment_parts.append(f"Reja qilingan sana: {body.travelDate}")
    if body.utm_source or body.utm_campaign:
        comment_parts.append(
            f"UTM: {body.utm_source or '—'} / {body.utm_medium or '—'} / {body.utm_campaign or '—'}"
        )

    data: dict[str, Any] = {
        "name": name,
        "phone": phone,
        "tour": body.destination.strip(),
        "people": 1,
        "amount": 0,
        "comment": " · ".join(comment_parts),
        "source": WEBSITE_DEFAULT_SOURCE,
        "stage": leads_router.STAGES[0],  # "Yangi" — standart boshlang'ich status
        "manager": assigned.get("name", "") if assigned else "",
        "ownerId": None,      # avtomatik yaratilgan — inson egasi yo'q
        "seenBy": [],         # hamma admin uchun "yangi" badge sifatida ko'rinadi
        "date": storage.today_uz(),
        "time": storage.now_time(),
        "createdAt": now_iso,
    }
    # Ariza formasidagi qo'shimcha maydonlar — mavjud Leads strukturasi
    # (name/phone/tour/stage/...) o'zgarmaydi, faqat qo'shimcha saqlanadi.
    if body.travelDate:
        data["travelDate"] = body.travelDate
    for field in ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"):
        value = getattr(body, field)
        if value:
            data[field] = value

    created = storage.insert("leads", data)
    leads_router._sync_sale(created)
    leads_router._sync_client(created)

    notify.log("Lead Form", "create", "lead", created.get("name", ""),
               {"amount": created.get("amount", 0)}, actor_id=None)
    notify.broadcast_admins(
        "Yangi lead (Sayt arizasi)",
        f"{created.get('name')} — {created.get('source')} · {created.get('tour') or '—'}",
        amount=float(created.get("amount") or 0),
        kind="lead",
        link="/ledlar",
    )

    return {"ok": True, "duplicate": False, "lead": created}