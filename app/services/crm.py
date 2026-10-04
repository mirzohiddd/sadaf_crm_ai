"""CRM qidiruv va statistika funksiyalari.

Bu modul dashboard, analitika, hisobot VA AI yordamchi uchun yagona manba.
Barcha funksiyalar `user` oladi va natijani uning ko'rish doirasiga qisqartiradi —
shuning uchun operator AI orqali ham begona ma'lumotni ololmaydi.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from .. import storage
from ..deps import is_global, scope_rows, visible_names

WON_STAGES = ("Bron tasdiqlandi", "To'lov qilindi")
LOST_STAGES = ("Bekor qilindi", "Sifatsiz lead")


# ——— Sana yordamchilari ———


def parse_uz(value: Any) -> date | None:
    """'18.05.2025' -> date. Tanib bo'lmasa None."""
    text = str(value or "").strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def in_last_days(value: Any, days: int) -> bool:
    d = parse_uz(value)
    if d is None:
        return False
    return d >= date.today() - timedelta(days=max(0, days - 1))


def is_today(value: Any) -> bool:
    return parse_uz(value) == date.today()


def is_this_month(value: Any) -> bool:
    d = parse_uz(value)
    today = date.today()
    return bool(d and d.year == today.year and d.month == today.month)


def money(n: Any) -> str:
    return "$" + f"{int(round(float(n or 0))):,}"


def pct(a: float, b: float) -> str:
    return f"{(a / b * 100):.1f}%" if b else "0%"


def _sum(rows: list[dict[str, Any]], field: str) -> float:
    return sum(float(r.get(field) or 0) for r in rows)


# ——— Scoped o'qish ———


def leads_of(user: dict[str, Any]) -> list[dict[str, Any]]:
    # "leads" resursi uchun scoping alohida: faqat Super Admin barchasini
    # ko'radi, oddiy Admin esa faqat o'ziga tegishli leadlarni (dashboard,
    # hisobot va AI yordamchida ham izchil bo'lishi uchun).
    return scope_rows(storage.read("leads"), user, resource="leads")


def clients_of(user: dict[str, Any]) -> list[dict[str, Any]]:
    return scope_rows(storage.read("clients"), user)


def sales_of(user: dict[str, Any]) -> list[dict[str, Any]]:
    return scope_rows(storage.read("sales"), user)


def tours_of(user: dict[str, Any]) -> list[dict[str, Any]]:
    # Turlar katalogi — hamma ko'radi
    return storage.read("tours")


def tasks_of(user: dict[str, Any]) -> list[dict[str, Any]]:
    rows = storage.read("tasks")
    if is_global(user):
        return rows
    names = visible_names(user) or set()
    return [t for t in rows if t.get("to") in names or t.get("from") in names]


def employees_of(user: dict[str, Any]) -> list[dict[str, Any]]:
    from ..deps import team_of

    return [{k: v for k, v in u.items() if k != "passwordHash"} for u in team_of(user)]


# ——— AI uchun qidiruv funksiyalari ———


_APOSTROPHES = str.maketrans({"\u2019": "'", "\u2018": "'", "`": "'", "\u02bb": "'", "\u02bc": "'"})


def norm(value: Any) -> str:
    """Taqqoslash uchun: kichik harf, bo'shliqsiz, o'zbekcha apostrof
    variantlari (ʻ ʼ ’ ‘ `) bitta ' ga keltiriladi."""
    return str(value or "").translate(_APOSTROPHES).lower().strip()


def _eq(a: Any, b: Any) -> bool:
    return norm(a) == norm(b)


def _match(row: dict[str, Any], query: str, fields: tuple[str, ...]) -> bool:
    if not query:
        return True
    q = norm(query)
    return any(q in norm(row.get(f, "")) for f in fields)


def search_leads(
    user: dict[str, Any],
    query: str = "",
    stage: str = "",
    source: str = "",
    period: str = "",
    limit: int = 25,
) -> list[dict[str, Any]]:
    rows = leads_of(user)
    rows = [r for r in rows if _match(r, query, ("name", "phone", "tour", "comment", "manager"))]
    if stage:
        rows = [r for r in rows if _eq(r.get("stage"), stage)]
    if source:
        rows = [r for r in rows if _eq(r.get("source"), source)]
    if period == "today":
        rows = [r for r in rows if is_today(r.get("date"))]
    elif period == "month":
        rows = [r for r in rows if is_this_month(r.get("date"))]
    elif period == "week":
        rows = [r for r in rows if in_last_days(r.get("date"), 7)]
    return rows[: max(1, limit)]


def search_clients(
    user: dict[str, Any], query: str = "", country: str = "", status: str = "", limit: int = 25
) -> list[dict[str, Any]]:
    rows = clients_of(user)
    rows = [r for r in rows if _match(r, query, ("name", "phone", "country", "lastTour", "manager"))]
    if country:
        rows = [r for r in rows if _eq(r.get("country"), country)]
    if status:
        rows = [r for r in rows if _eq(r.get("status"), status)]
    return rows[: max(1, limit)]


def search_sales(
    user: dict[str, Any], query: str = "", period: str = "", limit: int = 25
) -> list[dict[str, Any]]:
    rows = sales_of(user)
    rows = [r for r in rows if _match(r, query, ("client", "tour", "manager"))]
    if period == "today":
        rows = [r for r in rows if is_today(r.get("date"))]
    elif period == "month":
        rows = [r for r in rows if is_this_month(r.get("date"))]
    elif period == "week":
        rows = [r for r in rows if in_last_days(r.get("date"), 7)]
    return rows[: max(1, limit)]


def search_tasks(
    user: dict[str, Any], query: str = "", only_open: bool = False, mine: bool = False, limit: int = 25
) -> list[dict[str, Any]]:
    rows = tasks_of(user)
    rows = [r for r in rows if _match(r, query, ("title", "to", "from", "priority"))]
    if only_open:
        rows = [r for r in rows if not r.get("done")]
    if mine:
        rows = [r for r in rows if r.get("to") == user.get("name")]
    return rows[: max(1, limit)]


# ——— Statistika ———


def get_dashboard_stats(user: dict[str, Any], period: str = "month") -> dict[str, Any]:
    """Dashboard uchun barcha ko'rsatkichlar. Har bir hisob o'z doirasida."""
    leads = leads_of(user)
    clients = clients_of(user)
    tours = tours_of(user)
    tasks = tasks_of(user)

    won = [l for l in leads if l.get("stage") in WON_STAGES]
    lost = [l for l in leads if l.get("stage") in LOST_STAGES]
    month_won = [l for l in won if is_this_month(l.get("date"))]

    revenue = _sum(month_won, "amount")
    avg_check = revenue / len(month_won) if month_won else 0

    today_leads = [l for l in leads if is_today(l.get("date"))]
    today_clients = [c for c in clients if is_today(c.get("createdDate") or c.get("lastDate"))]
    active_tours = [t for t in tours if t.get("status") == "Faol"]

    stats = [
        {"key": "leads", "label": "Yangi ledlar", "value": str(len(today_leads)),
         "delta": pct(len(today_leads), len(leads)), "period": "bugun",
         "icon": "users", "color": "blue"},
        {"key": "tours", "label": "Faol turlar", "value": str(len(active_tours)),
         "delta": pct(len(active_tours), len(tours)), "period": "bugun",
         "icon": "bag", "color": "green"},
        {"key": "revenue", "label": "Jami savdo", "value": money(revenue),
         "delta": pct(len(month_won), len(leads)), "period": "bu oy",
         "icon": "dollar", "color": "amber"},
        {"key": "avg", "label": "O'rtacha chek", "value": money(avg_check),
         "delta": "", "period": "bu oy", "icon": "chart", "color": "violet"},
        {"key": "clients", "label": "Yangi mijozlar", "value": str(len(today_clients)),
         "delta": pct(len(today_clients), len(clients)), "period": "bugun",
         "icon": "user", "color": "teal"},
    ]

    # Manbalar bo'yicha taqsimot
    source_colors = {
        "Telegram": "#3b82f6", "Instagram": "#ec4899",
        "Sayt": "#10b981", "Qo'ng'iroq": "#f59e0b",
    }
    counts: dict[str, int] = defaultdict(int)
    for l in leads:
        counts[str(l.get("source") or "Boshqa")] += 1
    lead_sources = [
        {"label": k, "value": v, "color": source_colors.get(k, "#94a3b8")}
        for k, v in sorted(counts.items(), key=lambda kv: -kv[1])
    ]

    # Savdo dinamikasi — oxirgi 14 kun
    series_map: dict[str, float] = {}
    for i in range(13, -1, -1):
        d = date.today() - timedelta(days=i)
        series_map[d.strftime("%d.%m")] = 0.0
    for l in won:
        d = parse_uz(l.get("date"))
        if d and (date.today() - d).days < 14:
            key = d.strftime("%d.%m")
            series_map[key] = series_map.get(key, 0) + float(l.get("amount") or 0)

    # Top yo'nalishlar
    tour_country = {t.get("name"): t.get("country") for t in tours}
    directions: dict[str, float] = defaultdict(float)
    for l in won:
        key = tour_country.get(l.get("tour")) or l.get("tour") or "Boshqa"
        directions[key] += float(l.get("amount") or 0)
    top_directions = [
        {"name": k, "amount": money(v)}
        for k, v in sorted(directions.items(), key=lambda kv: -kv[1])[:5]
    ]

    # So'nggi leadlar / bronlar
    recent_leads = [
        {"name": l.get("name"), "source": l.get("source"),
         "date": l.get("date"), "time": l.get("time", "")}
        for l in leads[:5]
    ]
    bookings = [
        {"name": l.get("name"), "direction": l.get("tour"),
         "status": l.get("stage"), "amount": money(l.get("amount"))}
        for l in won[:5]
    ]

    return {
        "stats": stats,
        "leadSources": lead_sources,
        "salesSeries": {"labels": list(series_map.keys()), "points": list(series_map.values())},
        "topDirections": top_directions,
        "recentLeads": recent_leads,
        "bookings": bookings,
        "totals": {
            "leads": len(leads),
            "clients": len(clients),
            "tours": len(tours),
            "won": len(won),
            "lost": len(lost),
            "revenue": revenue,
            "pipeline": _sum(
                [l for l in leads if l.get("stage") not in WON_STAGES + LOST_STAGES], "amount"
            ),
            "conversion": pct(len(won), len(leads)),
            "openTasks": len([t for t in tasks if not t.get("done")]),
            "myOpenTasks": len(
                [t for t in tasks if not t.get("done") and t.get("to") == user.get("name")]
            ),
        },
        "scope": "all" if is_global(user) else ("team" if user.get("role") == "manager" else "own"),
    }


def get_employee_stats(user: dict[str, Any], name: str = "") -> dict[str, Any]:
    """Bitta hodim (yoki joriy foydalanuvchi) kesimidagi ko'rsatkichlar."""
    target = name or user.get("name", "")
    allowed = visible_names(user)
    if allowed is not None and target not in allowed:
        return {"error": "Bu hodim ma'lumotlari sizga ochiq emas.", "name": target}

    leads = [l for l in storage.read("leads") if l.get("manager") == target]
    won = [l for l in leads if l.get("stage") in WON_STAGES]
    lost = [l for l in leads if l.get("stage") in LOST_STAGES]
    clients = [c for c in storage.read("clients") if c.get("manager") == target]
    tasks = [t for t in storage.read("tasks") if t.get("to") == target]
    revenue = _sum(won, "amount")

    return {
        "name": target,
        "leads": len(leads),
        "won": len(won),
        "lost": len(lost),
        "clients": len(clients),
        "revenue": revenue,
        "revenueText": money(revenue),
        "conversion": pct(len(won), len(leads)),
        "avgCheck": money(revenue / len(won) if won else 0),
        "openTasks": len([t for t in tasks if not t.get("done")]),
    }


def get_analytics(user: dict[str, Any]) -> dict[str, Any]:
    """Analitika sahifasi uchun agregatlar."""
    leads = leads_of(user)
    tours = tours_of(user)
    won = [l for l in leads if l.get("stage") in WON_STAGES]
    revenue = _sum(won, "amount")

    by_date: dict[str, float] = defaultdict(float)
    for l in won:
        by_date[str(l.get("date") or "")] += float(l.get("amount") or 0)
    ordered = sorted(
        (k for k in by_date if k), key=lambda k: parse_uz(k) or date.min
    )

    by_source: dict[str, int] = defaultdict(int)
    for l in leads:
        by_source[str(l.get("source") or "Boshqa")] += 1

    stage_order = [
        "Yangi", "Bog'lanildi", "Taklif yuborildi", "To'lov qilindi", "Bron tasdiqlandi",
    ]
    funnel = [
        {"label": s, "value": len([l for l in leads if l.get("stage") == s])}
        for s in stage_order
    ]

    managers: dict[str, dict[str, float]] = defaultdict(lambda: {"deals": 0, "revenue": 0.0})
    for l in leads:
        m = str(l.get("manager") or "Biriktirilmagan")
        managers[m]["deals"] += 1
        if l.get("stage") in WON_STAGES:
            managers[m]["revenue"] += float(l.get("amount") or 0)

    tour_country = {t.get("name"): t.get("country") for t in tours}
    directions: dict[str, float] = defaultdict(float)
    for l in won:
        directions[tour_country.get(l.get("tour")) or l.get("tour") or "Boshqa"] += float(
            l.get("amount") or 0
        )

    return {
        "kpi": {
            "leads": len(leads),
            "pipeline": _sum(leads, "amount"),
            "revenue": revenue,
            "conversion": pct(len(won), len(leads)),
            "avg": revenue / len(won) if won else 0,
            "lost": len([l for l in leads if l.get("stage") in LOST_STAGES]),
        },
        "salesByDate": {"labels": ordered, "points": [by_date[k] for k in ordered]},
        "bySource": [
            {"label": k, "value": v}
            for k, v in sorted(by_source.items(), key=lambda kv: -kv[1])
        ],
        "funnel": funnel if any(f["value"] for f in funnel) else [],
        "managers": sorted(
            ({"name": k, **v} for k, v in managers.items()),
            key=lambda m: -m["revenue"],
        ),
        "byDirection": sorted(
            ({"name": k, "amount": v} for k, v in directions.items()),
            key=lambda d: -d["amount"],
        ),
    }