"""
core/autorizacion.py — Roles y permisos (docs/deploy/PLAN.md, fase 4).

Dos roles por instancia (una empresa por despliegue):

- `administrador`: los usuarios con login (JWT) y la clave de la app de escritorio. Hacen todo.
- `integracion`: los tokens `dte_…` de los sistemas que llaman a la API (punto de venta, tienda,
  ERP). Emiten, envían, consultan y gestionan certificados, negocios, CAF y folios.

`PERMITIDO_INTEGRACION` es la única lista de lo que puede un token: **lo que no está en ella
queda negado** (403). La revisa `core.auth.requerir_autenticacion` en cada ruta protegida, con la
plantilla de la ruta (`/api/v1/dte/documentos/{dte_id}`), no con la URL.

Un documento suelto lo ve el token solo si lo emitió él (`dtes.origen`); si no, 404, para no
revelar que existe.
"""
from __future__ import annotations

from typing import Optional

from core.errors import RecursoNoEncontrado

ROL_ADMINISTRADOR = "administrador"
ROL_INTEGRACION = "integracion"

# Prefijos (con todo lo que cuelga de ellos) que puede usar un token de integración.
_PREFIJOS_INTEGRACION = (
    "/api/v1/dte",           # emitir, previsualizar, enviar, PDF/XML de sus documentos
    "/api/v1/estado",        # estado en el SII
    "/api/v1/caf",           # cargar y ver CAF, siguiente folio
    "/api/v1/certificado",   # validar certificados
    "/api/v1/keystore",      # certificados y negocios guardados (lo que entrega secretos exige la clave de la app)
    "/api/v1/monitoreo",     # folios restantes
    "/api/v1/valores",       # UF, UTM, dólar
)
# Rutas sueltas, con su método.
_RUTAS_INTEGRACION = {
    ("GET", "/api/v1/auth/me"),
    ("POST", "/api/v1/apikeys/propia/renovar"),
}
# Dentro de los prefijos permitidos, lo que igual queda para el administrador: el listado general
# de documentos y las alertas del despacho (un token ve solo los suyos, uno por uno).
_NEGADO_INTEGRACION = {
    ("GET", "/api/v1/dte/documentos"),
    ("GET", "/api/v1/dte/documentos/emisores"),
    ("GET", "/api/v1/dte/alertas"),
}


def _bajo(ruta: str, prefijo: str) -> bool:
    return ruta == prefijo or ruta.startswith(prefijo + "/")


def permite_integracion(metodo: str, ruta: str) -> bool:
    """¿Puede un token de integración usar `metodo ruta`? `ruta` es la plantilla de la ruta."""
    metodo = metodo.upper()
    if (metodo, ruta) in _RUTAS_INTEGRACION:
        return True
    if (metodo, ruta) in _NEGADO_INTEGRACION:
        return False
    return any(_bajo(ruta, p) for p in _PREFIJOS_INTEGRACION)


def es_token(usuario: Optional[dict]) -> bool:
    return bool(usuario) and usuario.get("tipo") == "apikey"


def actor(usuario: Optional[dict]) -> Optional[str]:
    """Quién actúa, para la auditoría y el dueño de lo que crea: `usuario:<id>` o `apikey:<id>`.
    Es el mismo formato que el `origen` de las emisiones."""
    if not usuario:
        return None
    if es_token(usuario):
        return f"apikey:{usuario['api_key_id']}"
    return f"usuario:{usuario['id']}" if usuario.get("id") is not None else None


def exigir_documento_propio(usuario: dict, dte_id: int) -> None:
    """Un token solo ve los documentos que emitió. Para el resto, 404 (no revela que existen).
    El administrador ve todos."""
    if not es_token(usuario):
        return
    from core.database import obtenerUno
    if not obtenerUno("SELECT 1 FROM dtes WHERE id=? AND origen=?", (dte_id, actor(usuario))):
        raise RecursoNoEncontrado(f"Documento {dte_id} no existe")
