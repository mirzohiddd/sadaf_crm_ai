"""Lead eslatmalari uchun fon vazifasi (background task).

Har 20 soniyada barcha eslatmalarni tekshiradi: muddati (``dueAt``) kelib,
hali ``notified`` qilinmagan eslatmalarni topib, ularni ``notified=True``
deb belgilaydi va tegishli foydalanuvchiga (eslatmani yaratgan/egasiga)
``/api/notifications`` orqali bildirishnoma yuboradi.

Eslatma: bu vazifa eslatmani "bajarilgan" (``done``) deb belgilamaydi —
faqat xabar berilganini (``notified``) belgilaydi. "Bajarildi" holatini
foydalanuvchi o'zi ``PATCH /api/reminders/{id}/done`` orqali belgilaydi.

Vaqt solishtirish doim Asia/Tashkent (UTC+5) bo'yicha amalga oshiriladi —
``storage.now_tashkent()`` orqali — shu tufayli server qayerda joylashgan
bo'lishidan (masalan Render — odatda UTC) qat'i nazar eslatmalar aynan
foydalanuvchi kiritgan sana/vaqtda ishga tushadi.

"Muddati kelganlarni topish + notified=True belgilash" bitta atomik
operatsiya (``storage.mutate`` — bitta lock, bitta yozuv) ichida
bajariladi. Shu tufayli bitta eslatma uchun ikki marta bildirishnoma
yuborilmaydi (masalan fon vazifasi ustma-ust chaqirilib qolsa ham).
"""
from __future__ import annotations

import asyncio
from typing import Any

from .. import storage
from . import notify

CHECK_INTERVAL_SECONDS = 20


def check_due() -> list[dict[str, Any]]:
    """Muddati kelgan va hali xabar berilmagan eslatmalarni topib,
    ``notified=True`` deb belgilaydi va bildirishnoma yuboradi.

    Xabar berilgan eslatmalar ro'yxatini qaytaradi (testlash uchun qulay).
    """
    now = storage.now_tashkent().replace(tzinfo=None).isoformat(timespec="minutes")

    def _flip_due(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """``rows`` ichida muddati kelganlarni joyida ``notified=True``
        deb belgilaydi va ularning nusxalari ro'yxatini qaytaradi.

        Shu funksiya ``storage.mutate`` ichida, bitta lock ostida
        ishlaydi — shuning uchun "tekshirish" va "belgilash" orasida
        boshqa chaqiruv aralashib, bir xil eslatmani ikki marta
        "topib" qo'ymaydi (duplicate bildirishnomaning oldini oladi).
        """
        flipped: list[dict[str, Any]] = []
        for row in rows:
            if row.get("done") or row.get("notified"):
                continue
            if str(row.get("dueAt") or "") > now:
                continue
            row["notified"] = True
            row["updatedAt"] = storage.now_iso()
            flipped.append(dict(row))
        return flipped

    due = storage.mutate("reminders", _flip_due)

    for reminder in due:
        owner_id = reminder.get("ownerId")
        lead_name = reminder.get("leadName") or "Lead"
        note = reminder.get("note") or ""
        notify.push(
            [owner_id] if owner_id is not None else [],
            title=f"Eslatma: {lead_name}",
            detail=note,
            kind="reminder",
            link=f"/ledlar?lead={reminder.get('leadId')}",
        )
    return due


async def run_forever() -> None:
    """Server ishlab turgan davomida davriy ravishda ``check_due()`` ni
    chaqiradi. Bitta tekshiruvdagi kutilmagan xato butun vazifani
    to'xtatmasin uchun ``try/except`` bilan o'raladi.

    Fon vazifasi ``main.py``dagi FastAPI ``lifespan`` ichida boshlanadi —
    shuning uchun server (Render'da ham) ishga tushgan/uyg'ongan zahoti
    darhol birinchi ``check_due()`` chaqiriladi (pastda ``while True``
    ichida sleep'dan OLDIN), ya'ni kechikkan eslatmalar ham darhol
    bildirishnoma sifatida yuboriladi.
    """
    while True:
        try:
            check_due()
        except Exception as exc:  # noqa: BLE001
            print(f"[sadaf] Eslatmalarni tekshirishda xato: {exc}")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)