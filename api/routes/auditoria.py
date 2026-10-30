"""
api/routes/auditoria.py — Consulta de la auditoría (antes `GET /api/v1/db/logs`).

Solo el administrador: un token no lee el registro que lo vigila (fase 4 de docs/deploy/PLAN.md).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from core.auth import requerir_administrador
from core.models import listar_logs

router = APIRouter(prefix="/api/v1/auditoria", tags=["Auditoría"])


class RegistroAuditoria(BaseModel):
    id: int
    accion: str
    actor: Optional[str] = None
    metodo: Optional[str] = None
    ruta: Optional[str] = None
    estado_http: Optional[int] = None
    ip_origen: Optional[str] = None
    tipo_dte: Optional[int] = None
    folio: Optional[int] = None
    rut_emisor: Optional[str] = None
    detalle: Optional[str] = None
    creado_en: str


@router.get("", response_model=list[RegistroAuditoria], summary="Quién hizo qué, del más reciente al más antiguo")
def consultar(
    actor: Optional[str] = Query(None, description="usuario:<id> o apikey:<id>"),
    accion: Optional[str] = Query(None, description="peticion, login, login_fallido, …"),
    rut_emisor: Optional[str] = Query(None),
    fecha_desde: Optional[str] = Query(None, description="ISO, p. ej. 2026-10-30"),
    limite: int = Query(100, ge=1, le=1000),
    usuario: dict = Depends(requerir_administrador),
) -> list[RegistroAuditoria]:
    return [RegistroAuditoria(**r) for r in listar_logs(
        accion=accion, rut_emisor=rut_emisor, fecha_desde=fecha_desde, limite=limite, actor=actor)]
