"""Lead assignment — leadlarni faol menejerlar orasida navbat bilan
(round-robin) taqsimlash.

Holat (oxirgi biriktirilgan menejer ID) alohida "lead_assignment"
kolleksiyasida (JSON faylda) saqlanadi — shuning uchun server qayta ishga
tushganda ham navbat 1-menejerdan qaytadan boshlanmaydi, oxirgi to'xtagan
joyidan davom etadi.

Navbatga kiradi: roli "admin" (Menejer) yoki "super_admin" (Bosh menejer)
bo'lgan FAOL foydalanuvchilar, ID bo'yicha barqaror tartibda.
.env dagi ROUND_ROBIN_EXCLUDE_LOGINS dagi loginlar navbatga kirmaydi.

Lead kimga biriktirilgani — ``lead.manager`` maydoni (menejer ismi);
ko'rish huquqi ham shu maydonga qarab tekshiriladi (deps.owns).
"""
from __future__ import annotations

from typing import Any

from .. import config, storage

STATE_ID = 1
ROTATION_ROLES = ("admin", "super_admin")


def _ensure_state(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Navbat holati yozuvini topadi; bo'lmasa — ``rows`` ichida yaratadi."""
    for row in rows:
        if int(row.get("id", 0)) == STATE_ID:
            return row
    row = {"id": STATE_ID, "lastAdminId": None}
    rows.append(row)
    return row


def _get_state() -> dict[str, Any]:
    return storage.mutate("lead_assignment", lambda rows: dict(_ensure_state(rows)))


def active_admins() -> list[dict[str, Any]]:
    """Round-robinga kiruvchi faol menejerlar, ID bo'yicha barqaror tartiblangan."""
    excluded = config.ROUND_ROBIN_EXCLUDE_LOGINS
    admins = [
        u for u in storage.read("users")
        if u.get("role") in ROTATION_ROLES
        and u.get("active", True)
        and str(u.get("login") or "").strip().lower() not in excluded
    ]
    admins.sort(key=lambda u: int(u.get("id", 0)))
    return admins


def _rotate_index(admins: list[dict[str, Any]], last_admin_id: int | None) -> int:
    """Oxirgi biriktirilgan menejer ID'dan keyingi navbatdagi indeksni topadi.

    Agar oxirgi menejer endi ro'yxatda bo'lmasa (masalan, faolsizlantirilgan
    yoki o'chirilgan) — navbat ro'yxat boshidan davom etadi.
    """
    ids = [int(a["id"]) for a in admins]
    if last_admin_id is not None and int(last_admin_id) in ids:
        return (ids.index(int(last_admin_id)) + 1) % len(ids)
    return 0


def peek_next_admin() -> dict[str, Any] | None:
    """Holatni o'zgartirmasdan — navbatda kim turganini ko'rsatadi."""
    admins = active_admins()
    if not admins:
        return None
    return admins[_rotate_index(admins, _get_state().get("lastAdminId"))]


def next_admin() -> dict[str, Any] | None:
    """Navbatdagi faol menejerni qaytaradi VA holatni saqlaydi (navbat bir
    qadam siljiydi). Faol menejer topilmasa — None.

    O'qish va yozish bitta lock ichida — bir vaqtda kelgan ikkita lead
    bitta menejerga tushib qolmaydi.
    """
    admins = active_admins()
    if not admins:
        return None

    def _do(rows: list[dict[str, Any]]) -> dict[str, Any]:
        state = _ensure_state(rows)
        chosen = admins[_rotate_index(admins, state.get("lastAdminId"))]
        state["lastAdminId"] = int(chosen["id"])
        state["updatedAt"] = storage.now_iso()
        return chosen

    return storage.mutate("lead_assignment", _do)


def resolve_manager(name: str) -> str:
    """Tashqi manbadan (Google Sheets) kelgan menejer ismini CRM hodimiga moslaydi.

    Faol menejer ismiga (katta-kichik harfsiz) aynan mos kelsa — uning CRM'dagi
    ismi, aks holda "" (bunday lead round-robin bilan biriktiriladi).
    """
    wanted = (name or "").strip().lower()
    if not wanted:
        return ""
    for admin in active_admins():
        if str(admin.get("name") or "").strip().lower() == wanted:
            return str(admin.get("name"))
    return ""


def rebalance(actor: dict[str, Any], dry_run: bool = False) -> dict[str, Any]:
    """Barcha mavjud leadlarni faol menejerlar orasida round-robin bilan qayta taqsimlaydi.

    - Leadlar ID bo'yicha (eskisidan yangisiga) navbat bilan: 1-lead → 1-menejer,
      2-lead → 2-menejer, ... — har bir menejer soni farqi ko'pi bilan 1 ta.
    - Leadda FAQAT ``manager`` maydoni o'zgaradi (o'zgargan leadda ``updatedAt`` ham).
    - Oxirgi lead olgan menejer navbat holatiga yoziladi — keyingi yangi lead
      undan keyingi menejerga tushadi.
    - Oldingi biriktirishlar zaxira sifatida navbat holatida saqlanadi
      (``rebalanceBackup``: leadId → eski menejer).
    - ``dry_run=True`` — hech narsa saqlanmaydi, faqat natija ko'rsatiladi.
    """
    admins = active_admins()
    if not admins:
        return {"success": False, "total": 0, "managers": [], "error": "Faol menejer topilmadi."}

    names = [str(a.get("name") or "") for a in admins]
    counts = [0] * len(admins)

    def _assign(rows: list[dict[str, Any]]) -> dict[str, Any]:
        ordered = sorted(rows, key=lambda r: int(r.get("id", 0)))
        previous: dict[str, str] = {}
        changed = 0
        now = storage.now_iso()
        for i, lead in enumerate(ordered):
            target = names[i % len(names)]
            counts[i % len(names)] += 1
            if lead.get("manager") == target:
                continue
            changed += 1  # dry_run'da ham — nechta lead o'zgarishini ko'rsatadi
            if not dry_run:
                previous[str(lead["id"])] = str(lead.get("manager") or "")
                lead["manager"] = target
                lead["updatedAt"] = now
        return {"total": len(ordered), "changed": changed, "previous": previous}

    if dry_run:
        result = _assign([dict(r) for r in storage.read("leads")])
    else:
        result = storage.mutate("leads", _assign)

    last_index = (result["total"] - 1) % len(admins) if result["total"] else None
    if not dry_run and last_index is not None:
        def _save_state(rows: list[dict[str, Any]]) -> None:
            state = _ensure_state(rows)
            state["lastAdminId"] = int(admins[last_index]["id"])
            state["updatedAt"] = storage.now_iso()
            state["lastRebalance"] = {
                "at": storage.now_iso(),
                "by": actor.get("name", ""),
                "total": result["total"],
                "changed": result["changed"],
            }
            state["rebalanceBackup"] = result["previous"]

        storage.mutate("lead_assignment", _save_state)

    nxt = admins[(last_index + 1) % len(admins)] if last_index is not None else admins[0]
    return {
        "success": True,
        "dryRun": dry_run,
        "total": result["total"],
        "changed": result["changed"],
        "managers": [
            {"id": int(a["id"]), "name": a.get("name", ""), "role": a.get("role"), "leads": counts[i]}
            for i, a in enumerate(admins)
        ],
        "nextManager": {"id": int(nxt["id"]), "name": nxt.get("name", "")},
    }