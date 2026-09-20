from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from .. import monitoring
from ..context import AppContext, get_ctx
from ..models import Alert, AlertStatus
from ..schemas import AlertOut

router = APIRouter(prefix="/api", tags=["monitoring"])


@router.get("/monitoring/reconciliation")
def reconciliation(
    breaks_only: bool = False, limit: int = 50, ctx: AppContext = Depends(get_ctx)
) -> list[dict[str, Any]]:
    with ctx.db.session() as s:
        return monitoring.reconciliation(s, ctx.settings, limit=min(limit, 500),
                                         breaks_only=breaks_only)  # fmt: skip


@router.get("/monitoring/summary")
def summary(ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    with ctx.db.session() as s:
        return monitoring.summary(s, ctx.settings)


@router.get("/alerts", response_model=list[AlertOut])
def list_alerts(
    status: str | None = None, limit: int = 100, ctx: AppContext = Depends(get_ctx)
) -> list[AlertOut]:
    stmt = select(Alert).order_by(Alert.id.desc()).limit(min(limit, 500))
    if status:
        stmt = stmt.where(Alert.status == status)
    with ctx.db.session() as s:
        return [AlertOut.model_validate(a) for a in s.scalars(stmt)]


@router.post("/alerts/{alert_id}/ack", response_model=AlertOut)
def acknowledge_alert(alert_id: int, ctx: AppContext = Depends(get_ctx)) -> AlertOut:
    with ctx.db.session() as s:
        alert = s.get(Alert, alert_id)
        if alert is None:
            raise HTTPException(404, "alert not found")
        alert.status = AlertStatus.ACKNOWLEDGED
        return AlertOut.model_validate(alert)
