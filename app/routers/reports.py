"""/api/reports — Excel export."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from ..deps import require
from ..services import excel
from ..services.crm import get_dashboard_stats

router = APIRouter(prefix="/api/reports", tags=["reports"])

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("")
def list_reports(user: dict[str, Any] = Depends(require("reports", "read"))) -> dict[str, Any]:
    """Mavjud hisobotlar va har birida nechta yozuv borligi."""
    totals = get_dashboard_stats(user)["totals"]
    items = []
    for report in excel.available_reports():
        rows = excel.rows_for(report["key"], user)
        items.append(
            {
                **report,
                "count": len(rows),
                "ready": bool(rows),
                "period": datetime.now().strftime("%m.%Y"),
            }
        )
    return {"reports": items, "totals": totals}


@router.get("/excel")
def export_excel(
    kind: str = Query("leads", description="Hisobot turi yoki 'all'"),
    user: dict[str, Any] = Depends(require("reports", "read")),
) -> Response:
    kinds = list(excel.REPORTS.keys()) if kind == "all" else [kind]
    unknown = [k for k in kinds if k not in excel.REPORTS]
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Noma'lum hisobot: {', '.join(unknown)}")

    payload = excel.build_workbook(kinds, user)
    stamp = datetime.now().strftime("%Y-%m-%d")
    filename = f"sadaf-{kind}-{stamp}.xlsx"

    return Response(
        content=payload,
        media_type=XLSX_MIME,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )

