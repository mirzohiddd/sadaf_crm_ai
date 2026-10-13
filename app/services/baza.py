"""PostgreSQL "BAZA" — Leadlar Kanbanidagi BAZA ustuni uchun ma'lumot manbai.

CRM'ning asosiy ma'lumotlari avvalgidek JSON fayllarda qoladi. Bu modul faqat
``DATABASE_URL`` orqali ulangan PostgreSQL jadvalidagi (standart:
``baza_leads``) leadlarni o'qiydi va ularni CRM leadiga aylantirishda
(Kanbanda boshqa ustunga sudrab o'tkazish) yordam beradi.

Takrorlanishga qarshi himoya (ustunda bir xil lead ikki marta chiqmaydi):

1. Jadval ichida — telefon raqami bo'yicha (formatdan qat'i nazar, oxirgi
   9 raqam): bir xil raqamli bir nechta yozuvdan faqat ENG BIRINCHI kelgani
   ko'rsatiladi. Telefonsiz yozuvlar ``id`` bo'yicha alohida.
2. CRM bilan — telefoni CRM'dagi biror lead bilan bir xil bo'lgan yoki
   allaqachon CRM'ga o'tkazilgan (``lead.bazaId``) yozuvlar ko'rsatilmaydi.
3. Avtomatik yaratilgan jadvalda telefon bo'yicha UNIQUE indeks bor.

Mavjud (o'zingizning) jadvalingiz bo'lsa — ustunlar nomi bo'yicha avtomatik
topiladi (pastdagi ``ALIASES``). Faqat ``id`` va ``name``/``phone`` dan biri shart.
"""
from __future__ import annotations

import logging
import re
import threading
import time as _time
from datetime import date, datetime
from typing import Any

from .. import config, storage
from . import arrival

logger = logging.getLogger("sadaf.baza")

try:  # psycopg ixtiyoriy: o'rnatilmagan bo'lsa BAZA "ulanmagan" holatda qoladi
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover
    psycopg = None  # type: ignore[assignment]


class BazaError(RuntimeError):
    """PostgreSQL bilan bog'liq, foydalanuvchiga ko'rsatsa bo'ladigan xato."""


# CRM maydoni → jadvaldagi mumkin bo'lgan ustun nomlari (kichik harfda).
ALIASES: dict[str, tuple[str, ...]] = {
    "id": ("id", "lead_id", "baza_id"),
    "name": ("name", "full_name", "fullname", "ism", "ism_familiya", "fio", "client_name", "client"),
    "phone": ("phone", "phone_number", "telefon", "telefon_raqam", "tel", "mobile", "phone1"),
    "source": ("source", "manba", "platform"),
    "tour": ("tour", "tur", "qaysi_tur", "destination"),
    "city": ("city", "shahar"),
    "telegram": ("telegram", "telegram_username"),
    "comment": ("comment", "izoh", "kommentariya", "note", "notes"),
    "createdAt": ("created_at", "created_time", "createdat", "arrived_at", "created", "date", "sana"),
    "crmLeadId": ("crm_lead_id",),
}
TEXT_FIELDS = ("name", "phone", "source", "tour", "city", "telegram", "comment")


def phone_key(phone: Any) -> str:
    """Telefonni solishtirish kaliti: faqat raqamlar, oxirgi 9 tasi
    (+998 90 123 45 67, 998901234567 va 901234567 — bir xil kalit)."""
    digits = re.sub(r"\D", "", re.sub(r"^\s*p:", "", str(phone or ""), flags=re.I))
    return digits[-9:] if len(digits) >= 9 else digits


def enabled() -> bool:
    return config.baza_enabled() and psycopg is not None


def _table_parts() -> tuple[str, str]:
    raw = config.BAZA_TABLE.strip().strip('"')
    if "." in raw:
        schema, table = raw.split(".", 1)
        return schema.strip('"') or "public", table.strip('"')
    return "public", raw


def _table_ident() -> "sql.Composed":
    schema, table = _table_parts()
    return sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier(table))


def _connect():
    if psycopg is None:
        raise BazaError("psycopg kutubxonasi o'rnatilmagan (pip install -r requirements.txt).")
    try:
        return psycopg.connect(
            config.DATABASE_URL,
            connect_timeout=config.BAZA_CONNECT_TIMEOUT,
            row_factory=dict_row,
            application_name="sadaf-crm",
        )
    except psycopg.Error as exc:
        logger.error("PostgreSQL ulanish xatosi: %s", exc)
        raise BazaError("PostgreSQL bazasiga ulanib bo'lmadi. DATABASE_URL ni tekshiring.") from exc


# ——— Jadval tuzilishi (kesh: 60 soniya) ———

_schema_lock = threading.Lock()
_schema_cache: dict[str, Any] = {"at": 0.0, "key": None, "value": None}


def _create_table(conn) -> None:
    t = _table_ident()
    _, table = _table_parts()
    conn.execute(sql.SQL(
        """CREATE TABLE IF NOT EXISTS {t} (
               id          BIGSERIAL PRIMARY KEY,
               name        TEXT NOT NULL DEFAULT '',
               phone       TEXT NOT NULL DEFAULT '',
               source      TEXT NOT NULL DEFAULT '',
               tour        TEXT NOT NULL DEFAULT '',
               city        TEXT NOT NULL DEFAULT '',
               telegram    TEXT NOT NULL DEFAULT '',
               comment     TEXT NOT NULL DEFAULT '',
               created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
               crm_lead_id INTEGER
           )"""
    ).format(t=t))
    # Bir telefon raqami jadvalga ikki marta yozilmasin (formatdan qat'i nazar).
    conn.execute(sql.SQL(
        "CREATE UNIQUE INDEX IF NOT EXISTS {idx} ON {t} "
        "((right(regexp_replace(phone, '\\D', '', 'g'), 9))) "
        "WHERE regexp_replace(phone, '\\D', '', 'g') <> ''"
    ).format(idx=sql.Identifier(f"{table}_phone_key_uniq"), t=t))
    conn.execute(sql.SQL("CREATE INDEX IF NOT EXISTS {idx} ON {t} (created_at)").format(
        idx=sql.Identifier(f"{table}_created_at_idx"), t=t))
    conn.commit()
    logger.info("BAZA jadvali yaratildi: %s", config.BAZA_TABLE)


def _columns(conn) -> dict[str, tuple[str, str]]:
    """CRM maydoni → (haqiqiy ustun nomi, data_type)."""
    schema, table = _table_parts()
    key = (config.DATABASE_URL, schema, table)
    with _schema_lock:
        if _schema_cache["key"] == key and _time.monotonic() - _schema_cache["at"] < 60:
            return _schema_cache["value"]

    def _load() -> list[dict[str, Any]]:
        return conn.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
            (schema, table),
        ).fetchall()

    cols = _load()
    if not cols and config.BAZA_AUTO_CREATE:
        _create_table(conn)
        cols = _load()
    if not cols:
        raise BazaError(f"PostgreSQL'da '{config.BAZA_TABLE}' jadvali topilmadi.")

    by_lower = {c["column_name"].lower(): (c["column_name"], c["data_type"]) for c in cols}
    mapping: dict[str, tuple[str, str]] = {}
    for field, names in ALIASES.items():
        for name in names:
            if name in by_lower:
                mapping[field] = by_lower[name]
                break
    if "id" not in mapping:
        raise BazaError(f"'{config.BAZA_TABLE}' jadvalida 'id' ustuni yo'q.")
    if "name" not in mapping and "phone" not in mapping:
        raise BazaError(f"'{config.BAZA_TABLE}' jadvalida ism (name) yoki telefon (phone) ustuni yo'q.")

    with _schema_lock:
        _schema_cache.update(at=_time.monotonic(), key=key, value=mapping)
    return mapping


def reset_cache() -> None:
    with _schema_lock:
        _schema_cache.update(at=0.0, key=None, value=None)


# ——— O'qish ———


def _select_list(cols: dict[str, tuple[str, str]]) -> "sql.Composed":
    parts = [sql.SQL("{}::text AS id").format(sql.Identifier(cols["id"][0]))]
    for field in TEXT_FIELDS:
        if field in cols:
            parts.append(sql.SQL("COALESCE({}::text, '') AS {}").format(
                sql.Identifier(cols[field][0]), sql.Identifier(field)))
        else:
            parts.append(sql.SQL("''::text AS {}").format(sql.Identifier(field)))
    if "createdAt" in cols:
        parts.append(sql.SQL("{} AS created_raw").format(sql.Identifier(cols["createdAt"][0])))
    else:
        parts.append(sql.SQL("NULL AS created_raw"))
    return sql.SQL(", ").join(parts)


def _day_expr(cols: dict[str, tuple[str, str]]) -> "sql.Composable | None":
    """Toshkent bo'yicha kun ifodasi (SQL'da filtrlash uchun). Matn ustun — None."""
    if "createdAt" not in cols:
        return None
    name, dtype = cols["createdAt"]
    col = sql.Identifier(name)
    if dtype == "timestamp with time zone":
        return sql.SQL("({} AT TIME ZONE 'Asia/Tashkent')::date").format(col)
    if dtype in ("timestamp without time zone", "date"):
        return sql.SQL("{}::date").format(col)  # zonasiz — Toshkent vaqti deb olinadi
    return None


def _arrival_of_raw(value: Any) -> str:
    parsed = arrival.parse_moment(value)
    return arrival.to_iso(parsed[0]) if parsed else ""


def _to_item(row: dict[str, Any]) -> dict[str, Any]:
    iso = _arrival_of_raw(row.get("created_raw"))
    has_time = not (isinstance(row.get("created_raw"), date) and not isinstance(row.get("created_raw"), datetime))
    day, hhmm = ("", "")
    if iso:
        parsed = arrival.parse_moment(iso)
        day, hhmm = arrival.display_parts(parsed[0], has_time) if parsed else ("", "")
    item = {k: str(row.get(k) or "").strip() for k in TEXT_FIELDS}
    item.update(id=str(row["id"]), arrivedAt=iso, date=day, time=hhmm)
    return item


def is_imported_from_current(lead: dict[str, Any]) -> bool:
    """Lead joriy BAZA jadvalidan o'tkazilganmi (``bazaId`` + ``bazaTable``)."""
    if lead.get("bazaId") in (None, ""):
        return False
    return str(lead.get("bazaTable") or config.BAZA_TABLE) == config.BAZA_TABLE


def _crm_exclusions() -> tuple[list[str], list[str]]:
    leads = storage.read("leads")
    keys = sorted({k for k in (phone_key(l.get("phone")) for l in leads) if k})
    ids = sorted({str(l["bazaId"]) for l in leads if is_imported_from_current(l)})
    return keys, ids


def list_leads(start: date | None = None, end: date | None = None, limit: int | None = None) -> dict[str, Any]:
    """BAZA ustuni uchun takrorlanmaydigan leadlar ro'yxati (eng yangisi tepada)."""
    limit = max(1, min(int(limit or config.BAZA_LIMIT), 5000))
    crm_keys, crm_ids = _crm_exclusions()
    with _connect() as conn:
        try:
            cols = _columns(conn)
            day = _day_expr(cols)
            where: list[sql.Composable] = [sql.SQL("TRUE")]
            params: dict[str, Any] = {"crm_keys": crm_keys, "crm_ids": crm_ids}
            if "crmLeadId" in cols:
                where.append(sql.SQL("{} IS NULL").format(sql.Identifier(cols["crmLeadId"][0])))
            python_filter = (start or end) and day is None
            if day is not None and start:
                where.append(sql.SQL("{} >= %(start)s").format(day))
                params["start"] = start
            if day is not None and end:
                where.append(sql.SQL("{} <= %(end)s").format(day))
                params["end"] = end
            created_order = (sql.SQL("{}").format(sql.Identifier(cols["createdAt"][0]))
                             if "createdAt" in cols else sql.SQL("NULL::int"))
            query = sql.SQL(
                """
                WITH src AS (
                    SELECT {select}, {created} AS created_sort,
                           regexp_replace(COALESCE({phone}, ''), '\\D', '', 'g') AS digits
                    FROM {table}
                    WHERE {where}
                ), keyed AS (
                    SELECT *, CASE WHEN length(digits) >= 9 THEN right(digits, 9) ELSE digits END AS pkey
                    FROM src
                ), dedup AS (
                    SELECT DISTINCT ON (COALESCE(NULLIF(pkey, ''), 'id:' || id)) *
                    FROM keyed
                    ORDER BY COALESCE(NULLIF(pkey, ''), 'id:' || id), created_sort ASC NULLS LAST, id
                )
                SELECT * FROM dedup
                WHERE NOT (pkey <> '' AND pkey = ANY(%(crm_keys)s::text[]))
                  AND NOT (id = ANY(%(crm_ids)s::text[]))
                ORDER BY created_sort DESC NULLS LAST, id DESC
                {limit}
                """
            ).format(
                select=_select_list(cols),
                created=created_order,
                phone=(sql.SQL("{}::text").format(sql.Identifier(cols["phone"][0]))
                       if "phone" in cols else sql.SQL("''")),
                table=_table_ident(),
                where=sql.SQL(" AND ").join(where),
                limit=sql.SQL("") if python_filter else sql.SQL("LIMIT {}").format(sql.Literal(limit + 1)),
            )
            rows = conn.execute(query, params).fetchall()
        except psycopg.Error as exc:
            logger.error("BAZA so'rovi xatosi: %s", exc)
            reset_cache()
            raise BazaError("PostgreSQL'dan leadlarni o'qib bo'lmadi.") from exc

    items = [_to_item(r) for r in rows]
    if python_filter:
        items = [i for i in items if arrival.in_range(i, start, end)]
    truncated = len(items) > limit
    return {"items": items[:limit], "truncated": truncated}


def get_row(row_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        try:
            cols = _columns(conn)
            row = conn.execute(
                sql.SQL("SELECT {select}{extra} FROM {table} WHERE {id}::text = %s LIMIT 1").format(
                    select=_select_list(cols),
                    extra=(sql.SQL(", {} AS crm_lead_id").format(sql.Identifier(cols["crmLeadId"][0]))
                           if "crmLeadId" in cols else sql.SQL(", NULL AS crm_lead_id")),
                    table=_table_ident(),
                    id=sql.Identifier(cols["id"][0]),
                ),
                (str(row_id),),
            ).fetchone()
        except psycopg.Error as exc:
            logger.error("BAZA so'rovi xatosi: %s", exc)
            raise BazaError("PostgreSQL'dan yozuvni o'qib bo'lmadi.") from exc
    if not row:
        return None
    item = _to_item(row)
    item["crmLeadId"] = row.get("crm_lead_id")
    return item


def mark_imported(row_id: str, lead_id: int) -> None:
    """Yozuv CRM'ga o'tkazilgani jadvalda belgilanadi (crm_lead_id ustuni bo'lsa).
    Ustun bo'lmasa ham muammo yo'q — CRM tomonida ``lead.bazaId`` saqlanadi."""
    try:
        with _connect() as conn:
            cols = _columns(conn)
            if "crmLeadId" not in cols:
                return
            conn.execute(
                sql.SQL("UPDATE {table} SET {col} = %s WHERE {id}::text = %s").format(
                    table=_table_ident(), col=sql.Identifier(cols["crmLeadId"][0]),
                    id=sql.Identifier(cols["id"][0])),
                (int(lead_id), str(row_id)),
            )
            conn.commit()
    except (BazaError, Exception) as exc:  # noqa: BLE001 — lead allaqachon yaratilgan
        logger.warning("BAZA yozuvini belgilab bo'lmadi (id=%s): %s", row_id, exc)
