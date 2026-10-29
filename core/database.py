"""
core/database.py

Gestión de base de datos para persistencia de DTEs, folios y logs.

Fachada estable para todo el motor. Desde la fase 1 del fork corre sobre SQLAlchemy
(`core/datos.py`), con SQLite o PostgreSQL según `DATABASE_URL`, y el esquema lo crean y
actualizan las migraciones de Alembic (`migraciones/`). Las consultas siguen escritas con `?`.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Generator, Optional

from core import datos
from core.config import settings
from core.datos import Conexion, Cursor, Fila


# Ruta del archivo SQLite (la usan las rutas de la app de escritorio, que comparten la base con
# el motor Go). None con PostgreSQL.
DATABASE_PATH: Optional[Path] = datos.ruta_sqlite()
_DATABASE_PATH_ORIGINAL = DATABASE_PATH  # si alguien reasigna DATABASE_PATH, core/datos.py lo sigue


def get_connection() -> Conexion:
    """
    Obtiene una conexión a la base de datos (quien la pide hace `commit` y `close`).

    Returns:
        Conexión con la forma de `sqlite3.Connection`.
    """
    return datos.conectar()


@contextmanager
def get_db() -> Generator[Conexion, None, None]:
    """
    Context manager para obtener una conexión a la BD.
    Confirma al salir, deshace si hay excepción y cierra la conexión.
    """
    with datos.transaccion() as conn:
        yield conn


def init_db() -> None:
    """
    Crea o actualiza el esquema con las migraciones de Alembic (`alembic upgrade head`).
    Se ejecuta al iniciar la aplicación; una base creada antes de Alembic se adopta sola.
    """
    from core import migrar
    migrar.actualizar()


def ejecutar(sql: str, params: tuple = ()) -> Cursor:
    """Ejecuta una consulta SQL."""
    with get_db() as conn:
        return conn.execute(sql, params)


def ejecutar_many(sql: str, params_list: list[tuple]) -> Cursor:
    """Ejecuta una consulta SQL con múltiples conjuntos de parámetros."""
    with get_db() as conn:
        return conn.executemany(sql, params_list)


def obtenerUno(sql: str, params: tuple = ()) -> Optional[Fila]:
    """Ejecuta una consulta y retorna un solo resultado."""
    with get_db() as conn:
        cursor = conn.execute(sql, params)
        return cursor.fetchone()


def obtener_todos(sql: str, params: tuple = ()) -> list[Fila]:
    """Ejecuta una consulta y retorna todos los resultados."""
    with get_db() as conn:
        cursor = conn.execute(sql, params)
        return cursor.fetchall()


# Esquema heredado: el que upstream crea al arrancar. Ya no se ejecuta al iniciar (lo reemplaza
# la migración 0001); se conserva para comparar en tests/unit/test_esquema.py y para adoptar
# bases creadas antes de Alembic (core/migrar.py).
SCHEMA_SQL = """
-- Tabla de documentos DTE emitidos
CREATE TABLE IF NOT EXISTS dtes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo_dte INTEGER NOT NULL,
    folio INTEGER NOT NULL,
    rut_emisor TEXT NOT NULL,
    rut_receptor TEXT NOT NULL,
    razon_social_receptor TEXT,
    fecha_emision TEXT NOT NULL,
    monto_neto INTEGER DEFAULT 0,
    monto_exento INTEGER DEFAULT 0,
    iva INTEGER DEFAULT 0,
    monto_total INTEGER NOT NULL,
    estado TEXT DEFAULT 'generado',
    track_id INTEGER,
    xml_firmado TEXT,
    pdf_path TEXT,
    ambiente TEXT DEFAULT 'certificacion',
    creado_en TEXT NOT NULL,
    actualizado_en TEXT NOT NULL,
    UNIQUE(rut_emisor, tipo_dte, folio)
);

-- Tabla de CAFs (Códigos de Autorización de Folios)
CREATE TABLE IF NOT EXISTS cafs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo_dte INTEGER NOT NULL,
    rut_emisor TEXT NOT NULL,
    folio_desde INTEGER NOT NULL,
    folio_hasta INTEGER NOT NULL,
    folio_siguiente INTEGER NOT NULL,
    fecha_autorizacion TEXT NOT NULL,
    caf_xml TEXT NOT NULL,
    activo INTEGER DEFAULT 1,
    creado_en TEXT NOT NULL,
    UNIQUE(rut_emisor, tipo_dte, folio_desde, folio_hasta)
);

-- Tabla de logs de auditoría
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    accion TEXT NOT NULL,
    tipo_dte INTEGER,
    folio INTEGER,
    rut_emisor TEXT,
    detalle TEXT,
    ip_origen TEXT,
    creado_en TEXT NOT NULL
);

-- Tabla de usuarios (para autenticación)
CREATE TABLE IF NOT EXISTS usuarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    hashed_password TEXT NOT NULL,
    nombre_completo TEXT,
    email TEXT,
    activo INTEGER DEFAULT 1,
    creado_en TEXT NOT NULL,
    ultimo_acceso TEXT
);

-- Registro de Ventas Diario (RVD / consumo de folios de boletas).
-- Una fila por (emisor, día, secuencia). El UNIQUE es lo que hace IDEMPOTENTE al
-- programador: si el servidor se reinicia o el bucle corre dos veces, el día ya
-- reportado no se vuelve a generar ni a enviar.
-- Para CORREGIR un día ya enviado se inserta una fila nueva con sec_envio+1 (el SII
-- espera el archivo completo de nuevo, no un diferencial).
CREATE TABLE IF NOT EXISTS rvd_envios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rut_emisor TEXT NOT NULL,
    fecha TEXT NOT NULL,
    sec_envio INTEGER NOT NULL DEFAULT 1,
    estado TEXT NOT NULL DEFAULT 'pendiente',
    track_id TEXT,
    xml_path TEXT,
    detalle TEXT,
    creado_en TEXT NOT NULL,
    actualizado_en TEXT,
    UNIQUE(rut_emisor, fecha, sec_envio)
);

-- Indicadores públicos del SII (UF, UTM, dólar), tabla de referencia que llena el núcleo Go
-- (`nucleo-valores actualizar|demonio` o la app al arrancar). Python solo la LEE
-- (api/routes/valores.py). Misma DDL que nucleo/valores/cache.go.
CREATE TABLE IF NOT EXISTS valores (
    indicador   TEXT NOT NULL,
    fecha       TEXT NOT NULL,
    valor       REAL NOT NULL,
    actualizado TEXT NOT NULL,
    PRIMARY KEY (indicador, fecha)
);

-- Índices para mejorar rendimiento
CREATE INDEX IF NOT EXISTS idx_rvd_rut_fecha ON rvd_envios(rut_emisor, fecha);
CREATE INDEX IF NOT EXISTS idx_dtes_tipo_folio ON dtes(tipo_dte, folio);
CREATE INDEX IF NOT EXISTS idx_dtes_rut_emisor ON dtes(rut_emisor);
CREATE INDEX IF NOT EXISTS idx_dtes_estado ON dtes(estado);
CREATE INDEX IF NOT EXISTS idx_dtes_fecha ON dtes(fecha_emision);
CREATE INDEX IF NOT EXISTS idx_cafs_rut_tipo ON cafs(rut_emisor, tipo_dte);
CREATE INDEX IF NOT EXISTS idx_audit_fecha ON audit_log(creado_en);
CREATE INDEX IF NOT EXISTS idx_audit_accion ON audit_log(accion);
"""
