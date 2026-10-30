"""
api/auditoria.py — Quién hizo qué (docs/deploy/PLAN.md, fase 4).

Cada petición a `/api/` que cambia algo (`POST`, `PUT`, `PATCH`, `DELETE`) deja una fila en
`audit_log`: actor (`usuario:<id>` o `apikey:<id>`, lo deja `core.auth` en `request.state`; vacío
si la credencial no valía), método, plantilla de la ruta, código HTTP e IP. **Nunca el cuerpo**:
puede traer claves o certificados. El login se registra en su propia ruta (éxito o fallo, con el
usuario intentado) y aquí se omite.

Se consulta en `GET /api/v1/auditoria` (solo el administrador).
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from starlette.concurrency import run_in_threadpool

from core.models import registrar_log

logger = logging.getLogger("dte.auditoria")

METODOS = {"POST", "PUT", "PATCH", "DELETE"}
_OMITIR = {"/api/v1/auth/login"}


def instalar(app: FastAPI) -> None:
    @app.middleware("http")
    async def _auditar(request: Request, call_next):
        respuesta = await call_next(request)
        ruta_url = request.url.path
        if request.method in METODOS and ruta_url.startswith("/api/") and ruta_url not in _OMITIR:
            ruta = getattr(request.scope.get("route"), "path", ruta_url)
            try:
                await run_in_threadpool(
                    registrar_log, accion="peticion", actor=getattr(request.state, "actor", None),
                    metodo=request.method, ruta=ruta, estado_http=respuesta.status_code,
                    ip_origen=request.client.host if request.client else None)
            except Exception:  # noqa: BLE001 — la auditoría no tumba la respuesta ya calculada
                logger.exception("no se pudo registrar la auditoría de %s %s", request.method, ruta)
        return respuesta
