"""
core/apikeys.py — Llaves de API (bearer estáticas) para integraciones y agentes.

Una API key es un secreto largo aleatorio con prefijo `dte_`. Se muestra **una sola vez** al
crearla; en BD se guarda solo su **hash SHA-256** (la clave es de alta entropía, no necesita
bcrypt). Se envía como `Authorization: Bearer dte_...` — `core/auth.py::requerir_autenticacion`
acepta indistintamente un JWT o una API key válida.

Gestión (crear/listar/renovar/revocar) en `api/routes/apikeys.py` + UI `static/apikeys.html`;
la hace el administrador. Un token solo puede renovarse a sí mismo (fase 4 de docs/deploy/PLAN.md):
**renovar** cambia el secreto y conserva el `id`, así su `id_externo` y su rastro en la auditoría
siguen siendo los mismos; la clave anterior muere al instante. **Revocar** lo corta para siempre.
Cada token tiene un rol (hoy `integracion`); sus permisos están en `core/autorizacion.py`.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from core import database
from core.autorizacion import ROL_INTEGRACION

_PREFIJO = "dte_"
# `ultimo_uso` se escribe como mucho una vez por minuto por token: cada petición lo leía y lo
# escribía, y con varios hilos eso es una escritura por petición.
_USO_CADA = timedelta(minutes=1)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_keys (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre     TEXT NOT NULL,
    prefijo    TEXT NOT NULL,
    key_hash   TEXT NOT NULL UNIQUE,
    activo     INTEGER NOT NULL DEFAULT 1,
    creado     TEXT NOT NULL,
    ultimo_uso TEXT
);
"""


def _hash(clave: str) -> str:
    return hashlib.sha256(clave.encode("utf-8")).hexdigest()


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_api_keys() -> None:
    with database.get_db() as conn:
        conn.execute(_SCHEMA)


def _clave_nueva() -> Tuple[str, str]:
    """`(clave_en_claro, prefijo)`. El prefijo ("dte_" + 8 chars) la identifica sin exponerla."""
    clave = _PREFIJO + secrets.token_urlsafe(32)
    return clave, clave[:12]


def crear_api_key(nombre: str, creado_por: Optional[int] = None) -> Tuple[str, dict]:
    """Crea una key y devuelve `(clave_en_claro, registro)`. La clave NO se puede recuperar
    después: se muestra una sola vez."""
    init_api_keys()
    clave, prefijo = _clave_nueva()
    ahora = _ahora()
    with database.get_db() as conn:
        cur = conn.execute(
            "INSERT INTO api_keys (nombre, prefijo, key_hash, activo, creado, rol, creado_por) "
            "VALUES (?,?,?,1,?,?,?)",
            (nombre, prefijo, _hash(clave), ahora, ROL_INTEGRACION, creado_por))
        kid = cur.lastrowid
    return clave, {"id": kid, "nombre": nombre, "prefijo": prefijo, "creado": ahora, "activo": True,
                   "rol": ROL_INTEGRACION}


def renovar_api_key(key_id: int) -> Optional[Tuple[str, dict]]:
    """Cambia el secreto de una key activa y conserva su `id`. La clave anterior deja de valer
    en el mismo instante. Devuelve `(clave_en_claro, registro)` o None si no existe o está revocada."""
    clave, prefijo = _clave_nueva()
    ahora = _ahora()
    with database.get_db() as conn:
        cur = conn.execute("UPDATE api_keys SET key_hash=?, prefijo=?, renovado=? WHERE id=? AND activo=1",
                           (_hash(clave), prefijo, ahora, key_id))
        if cur.rowcount == 0:
            return None
        fila = conn.execute("SELECT id, nombre, prefijo, rol, creado, renovado FROM api_keys WHERE id=?",
                            (key_id,)).fetchone()
    return clave, {**dict(fila), "activo": True}


def verificar_api_key(token: str) -> Optional[dict]:
    """Si `token` es una API key válida y activa, devuelve su registro (y marca uso). Si no
    parece una key (no tiene el prefijo), devuelve None sin tocar la BD."""
    if not token or not token.startswith(_PREFIJO):
        return None
    row = database.obtenerUno(
        "SELECT id, nombre, prefijo, rol, ultimo_uso FROM api_keys WHERE key_hash=? AND activo=1",
        (_hash(token),))
    if not row:
        return None
    ahora = datetime.now(timezone.utc)
    if not row["ultimo_uso"] or row["ultimo_uso"] < (ahora - _USO_CADA).isoformat():
        database.ejecutar("UPDATE api_keys SET ultimo_uso=? WHERE id=?", (ahora.isoformat(), row["id"]))
    return {"id": row["id"], "nombre": row["nombre"], "prefijo": row["prefijo"],
            "rol": row["rol"] or ROL_INTEGRACION}


def listar_api_keys() -> list:
    """Todas las keys (sin el hash ni la clave), para la UI."""
    init_api_keys()
    filas = database.obtener_todos(
        "SELECT id, nombre, prefijo, rol, activo, creado, renovado, ultimo_uso, creado_por "
        "FROM api_keys ORDER BY id DESC")
    return [dict(f) for f in filas]


def revocar_api_key(key_id: int) -> bool:
    with database.get_db() as conn:
        cur = conn.execute("UPDATE api_keys SET activo=0 WHERE id=?", (key_id,))
        return cur.rowcount > 0
