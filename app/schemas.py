"""So'rov/javob modellari.

CRM yozuvlari erkin shaklda (frontend qanday maydon yuborsa shuni saqlaymiz),
shuning uchun ko'p joyda `extra="allow"` ishlatilgan. Bu mavjud frontendni
o'zgartirmasdan ishlashga imkon beradi.
"""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Role = Literal["super_admin", "admin", "manager", "operator"]


class Loose(BaseModel):
    model_config = ConfigDict(extra="allow")


# ——— Auth ———


class LoginIn(BaseModel):
    login: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict[str, Any]


class PasswordChangeIn(BaseModel):
    current: str
    next: str = Field(min_length=6)


class ProfileIn(Loose):
    name: str | None = None
    email: str | None = None
    phone: str | None = None


# ——— Hodimlar ———


class EmployeeIn(Loose):
    name: str
    phone: str = ""
    position: str = ""
    birthDate: str = ""
    hireDate: str = ""
    shift: str = ""
    status: str = "Ishlayapti"
    address: str = ""
    deals: int = 0
    crmLogin: str
    crmPassword: str | None = None
    crmRole: Role = "operator"
    managerId: int | None = None


class EmployeeUpdate(Loose):
    crmPassword: str | None = None


# ——— CRM yozuvlari ———


class LeadIn(Loose):
    # Barcha maydonlar ixtiyoriy — frontend forma bo'sh maydonlar bilan ham
    # saqlashi mumkin, shuning uchun bu yerda `required` maydon yo'q.
    name: str = ""
    phone: str = ""
    tour: str = ""
    people: int = 0
    amount: float = 0
    manager: str = ""
    source: str = ""
    stage: str = "Yangi"


class StageIn(BaseModel):
    stage: str


class LeadCommentIn(BaseModel):
    """Leadga yangi kommentariya (11-bo'lim). Vaqt va muallif serverda qo'yiladi."""

    text: str = Field(min_length=1, max_length=2000)


def _sheet_number(value: Any) -> float | None:
    """Jadval katakchasidagi sonni o'qiydi. Aniq son bo'lmasa — None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = re.sub(r"[^\d.,-]", "", str(value))
    if not re.search(r"\d", text):
        return None
    if "," in text and "." in text:
        text = text.replace(",", "")
    elif "," in text:
        head, _, tail = text.rpartition(",")
        # "1,200" — minglik ajratgich; "12,5" — o'nlik kasr
        text = text.replace(",", "") if len(tail) == 3 and head else head.replace(",", "") + "." + tail
    try:
        return float(text)
    except ValueError:
        return None


class SheetLeadIn(Loose):
    """Google Sheets Apps Script yuboradigan bitta qator (yangi lead).

    Ikki xil jadval formatini qo'llab-quvvatlaydi:
      1. Oddiy/qo'lda to'ldiriladigan jadval — name, phone, tour, people,
         amount, comment, city, telegram, source.
      2. Meta (Facebook/Instagram) Lead Ads eksporti — qo'shimcha ravishda
         platform, campaign, ad, leadStatus, createdTime, formName,
         externalId maydonlarini yuboradi (pastga qarang).
    Ikkalasi ham bir vaqtda, ixtiyoriy tarzda ishlatilishi mumkin.
    """

    name: str
    phone: str
    # Jadvalda bo'lmagan maydonlar BO'SH qoladi — CRM hech narsani taxmin qilmaydi.
    tour: str = ""
    people: int | None = None   # "Nechta odam" — bo'sh bo'lsa None
    amount: float | None = None # "Summa (USD)" — bo'sh bo'lsa None
    manager: str = ""           # "Mas'ul menejer" — bo'sh bo'lsa biriktirilmaydi
    comment: str = ""
    city: str = ""
    telegram: str = ""
    source: str = ""            # bo'sh bo'lsa — platform ustunidan olinadi
    rowId: str | int | None = None  # Sheetdagi qator raqami/ID — kuzatuv uchun, ixtiyoriy
    sheet: str = ""             # qaysi varaqdan kelgani (Sheet1 / Sheet3) — kuzatuv uchun

    # ——— Meta (Facebook/Instagram) Lead Ads maydonlari — hammasi ixtiyoriy ———
    platform: str = ""          # jadvaldagi "platform" ustuni — masalan "ig", "fb"
    campaign: str = ""          # "campaign_name" ustuni
    ad: str = ""                # "ad_name" ustuni
    leadStatus: str = ""        # "lead_status" ustuni (Meta tomonidagi holat, masalan "CREATED")
    formName: str = ""          # "form_name" ustuni
    createdTime: str = ""       # "created_time" ustuni (ISO 8601) — lead yaratilgan sana/vaqt
    externalId: str = ""        # "id" ustuni — Metadagi lead identifikatori (dublikatni aniqlash uchun)

    @field_validator("tour", "manager", "comment", "city", "telegram", "source", "platform",
                     "campaign", "ad", "leadStatus", "formName", "createdTime", "externalId", "sheet",
                     mode="before")
    @classmethod
    def _text(cls, v: Any) -> str:
        """Jadval katakchasi raqam yoki bo'sh (null) bo'lishi mumkin — matnga keltiriladi."""
        if v is None:
            return ""
        if isinstance(v, float) and v.is_integer():
            v = int(v)
        return str(v).strip()

    @field_validator("name", "phone", mode="before")
    @classmethod
    def _required_text(cls, v: Any) -> str:
        if isinstance(v, float) and v.is_integer():
            v = int(v)
        return "" if v is None else str(v).strip()

    @field_validator("people", mode="before")
    @classmethod
    def _people(cls, v: Any) -> int | None:
        """"2", "2 kishi", 2.0 → 2. Bo'sh yoki raqamsiz qiymat → None (taxmin qilinmaydi)."""
        num = _sheet_number(v)
        return int(num) if num is not None and num > 0 else None

    @field_validator("amount", mode="before")
    @classmethod
    def _amount(cls, v: Any) -> float | None:
        """"1200", "1 200 $", "1,200.50" → son. Bo'sh/raqamsiz → None."""
        num = _sheet_number(v)
        return num if num is not None and num >= 0 else None


class WebsiteLeadIn(Loose):
    """Sayt (Lead Form / landing) dan kelgan ariza — `sadaf-landing` frontendi
    ``POST /api/integrations/website/lead`` ga shu ko'rinishda yuboradi
    (frontend: ``src/services/api.js`` -> ``submitLead``).

    - ``destination`` CRM'dagi mavjud ``tour`` maydoniga moslanadi.
    - ``travelDate`` alohida saqlanadi va izohga ham qo'shiladi — mavjud
      Leads jadvali/UI hech qanday o'zgarishsiz uni "Izoh" ustunida darhol
      ko'rsatadi.
    - utm_* maydonlari ixtiyoriy — targetolog/reklama manbasini aniqlash
      uchun leadga qo'shimcha maydon sifatida saqlanadi.
    """

    name: str
    phone: str
    destination: str = ""
    travelDate: str = ""

    utm_source: str = ""
    utm_medium: str = ""
    utm_campaign: str = ""
    utm_content: str = ""
    utm_term: str = ""


class ClientIn(Loose):
    name: str
    phone: str = ""
    country: str = ""


class TourIn(Loose):
    name: str
    country: str = ""
    days: int = 1
    price: float = 0
    seats: int = 0
    status: str = "Faol"


class SaleIn(Loose):
    client: str = ""
    tour: str = ""
    amount: float = 0
    manager: str = ""


class TaskIn(Loose):
    title: str
    to: str
    due: str = ""
    time: str = ""
    priority: str = "O'rta"
    done: bool = False


class TaskUpdate(Loose):
    pass


# ——— Lead eslatmalari (Reminder) ———


class ReminderIn(Loose):
    """Lead ichidan qo'yiladigan eslatma.

    ``date`` — ``YYYY-MM-DD`` (HTML ``<input type="date">`` formati),
    ``time`` — ``HH:MM`` (HTML ``<input type="time">`` formati).
    """

    date: str
    time: str = "09:00"
    note: str = ""


# ——— Sozlamalar / davomat / AI ———


class SettingsIn(Loose):
    pass


# ——— Analitika maqsadi ———


class AnalyticsGoalIn(BaseModel):
    monthlyTarget: float = Field(ge=0)


class AttendanceOut(BaseModel):
    model_config = ConfigDict(extra="allow")


class AiChatIn(BaseModel):
    message: str
    history: list[dict[str, Any]] = Field(default_factory=list)


class AiChatOut(BaseModel):
    title: str | None = None
    lines: list[str] = Field(default_factory=list)
    text: str = ""
    source: str = "local"


# ——— AI Call Center ———


class CallCreateIn(BaseModel):
    """Yangi qo'ng'iroq. Menejer (egasi) bu yerda YO'Q — u har doim
    backendda token orqali aniqlanadi; frontend yuborgan har qanday
    managerId/ownerId e'tiborsiz qoladi (ortiqcha maydonlar tashlab yuboriladi)."""

    model_config = ConfigDict(extra="ignore")

    leadId: int
    consent: bool = False
    direction: Literal["outbound", "inbound"] = "outbound"
