"""
core/datos.py — Acceso a la base con SQLAlchemy Core, para SQLite y PostgreSQL.

`core/database.py` sigue siendo la fachada que usa todo el motor (`get_db`, `obtenerUno`,
`ejecutar`…). Por debajo, desde la fase 1 del fork (docs/deploy/PLAN.md), corre sobre un
`Engine` de SQLAlchemy elegido por `DATABASE_URL`:

- `sqlite:///ruta.db`: mismo comportamiento que antes. Sin pool (una conexión por uso, como
  `sqlite3.connect`), WAL y claves foráneas al conectar, y el control de transacciones del
  propio `sqlite3` (BEGIN implícito antes de escribir).
- `postgresql+psycopg://usuario:clave@host/base`: pool de conexiones y transacción por uso.

`Conexion` imita la parte de `sqlite3.Connection` que usa el motor (`execute`, `executemany`,
`executescript`, `commit`, `rollback`, `close`, y cursores con `fetchone`, `fetchall`,
`rowcount` y `lastrowid`), así que las ~110 consultas escritas con `?` no cambian.

El esquema es de Alembic (`migraciones/`). El DDL heredado de upstream que los módulos corren
al vuelo (`CREATE TABLE IF NOT EXISTS`, `ALTER TABLE`, `PRAGMA table_info`) se ignora aquí;
`tests/unit/test_esquema.py` exige que ese DDL y las migraciones den el mismo esquema, así que
un cambio de esquema traído de upstream no pasa sin su migración.
"""
from __future__ import annotations

import logging
import re
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional, Sequence

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Connection, Engine, make_url
from sqlalchemy.pool import NullPool

from core.config import settings

logger = logging.getLogger("dte.datos")

_DDL_HEREDADO = re.compile(
    r"^\s*(?:--[^\n]*\n\s*)*(?:CREATE\s+(?:UNIQUE\s+)?(?:TABLE|INDEX)\s+IF\s+NOT\s+EXISTS|ALTER\s+TABLE|PRAGMA\s+table_info)",
    re.IGNORECASE)
_INSERT = re.compile(r"^\s*INSERT\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)

# Tablas con `id` autoincremental: en PostgreSQL su INSERT agrega `RETURNING id` para que
# `cursor.lastrowid` siga funcionando. Las demás tienen clave natural.
TABLAS_CON_ID = frozenset({"dtes", "cafs", "audit_log", "usuarios", "rvd_envios", "api_keys",
                           "certificados", "claves_sii", "negocios", "rcv_documentos"})

_motor: Optional[Engine] = None
_motor_url: Optional[str] = None
_url_forzada: Optional[str] = None
_candado = threading.Lock()
_ddl_heredado = threading.local()


def url() -> str:
    """`DATABASE_URL` normalizada: `postgres://` y `postgresql://` usan psycopg 3."""
    u = _url_forzada or settings.database_url
    for prefijo in ("postgres://", "postgresql://"):
        if u.startswith(prefijo):
            return "postgresql+psycopg://" + u[len(prefijo):]
    return u


def es_sqlite() -> bool:
    return url().startswith("sqlite")


def es_postgres() -> bool:
    return url().startswith("postgresql")


def url_segura() -> str:
    """La URL sin contraseña, para logs y respuestas."""
    return make_url(url()).render_as_string(hide_password=True)


def ruta_sqlite() -> Optional[Path]:
    """Ruta del archivo SQLite, o None si la base no es SQLite."""
    if not es_sqlite():
        return None
    return Path(make_url(url()).database or "")


def _url_efectiva() -> str:
    """La URL a usar ahora. Compatibilidad con upstream: varios scripts de prueba redirigen la
    base asignando `core.database.DATABASE_PATH` a un archivo temporal; si cambió, se respeta."""
    if _url_forzada is None:
        db = sys.modules.get("core.database")
        ruta = getattr(db, "DATABASE_PATH", None)
        if ruta is not None and ruta != getattr(db, "_DATABASE_PATH_ORIGINAL", ruta):
            return f"sqlite:///{Path(ruta).as_posix()}"
    return url()


def motor() -> Engine:
    """El `Engine` del proceso (se crea en el primer uso, y de nuevo si cambia la base)."""
    global _motor, _motor_url
    u = _url_efectiva()
    if _motor is None or _motor_url != u:
        with _candado:
            if _motor is None or _motor_url != u:
                if _motor is not None:
                    _motor.dispose()
                _motor, _motor_url = _crear_motor(u), u
    return _motor


def _crear_motor(u: str) -> Engine:
    if u.startswith("sqlite"):
        ruta = Path(make_url(u).database or "")
        if str(ruta) not in ("", ":memory:"):
            ruta.parent.mkdir(parents=True, exist_ok=True)
        eng = create_engine(u, poolclass=NullPool, connect_args={"check_same_thread": False})

        @event.listens_for(eng, "connect")
        def _al_conectar(dbapi, _registro):  # mismo arranque que tenía sqlite3.connect
            cur = dbapi.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
        return eng
    return create_engine(u, pool_pre_ping=True, pool_size=5, max_overflow=10)


def reiniciar_motor() -> None:
    """Descarta el `Engine` (lo usan las pruebas y las migraciones al cambiar de base)."""
    global _motor
    with _candado:
        if _motor is not None:
            _motor.dispose()
        _motor = None


@contextmanager
def con_url(otra: str) -> Iterator[None]:
    """Apunta el motor a otra base mientras dura el bloque (pruebas de migraciones)."""
    global _url_forzada
    anterior = _url_forzada
    _url_forzada = otra
    reiniciar_motor()
    try:
        yield
    finally:
        _url_forzada = anterior
        reiniciar_motor()


@contextmanager
def ddl_heredado() -> Iterator[None]:
    """Deja correr el DDL heredado de los módulos. Solo para armar una base heredada
    (antes de marcarla con Alembic) y para compararla en las pruebas."""
    _ddl_heredado.activo = True
    try:
        yield
    finally:
        _ddl_heredado.activo = False


def _es_ddl_heredado(sql: str) -> bool:
    return bool(_DDL_HEREDADO.match(sql)) and not getattr(_ddl_heredado, "activo", False)


def _a_psycopg(sql: str, con_parametros: bool = True) -> str:
    """`?` → `%s` fuera de literales, y `%` literal → `%%` (psycopg usa estilo `format`).
    Sin parámetros psycopg no interpreta `%`, así que entonces no se duplica."""
    if not con_parametros:
        return sql
    out, comilla = [], None
    for ch in sql:
        if comilla:
            out.append("%%" if ch == "%" else ch)
            if ch == comilla:
                comilla = None
        elif ch in ("'", '"'):
            comilla = ch
            out.append(ch)
        elif ch == "?":
            out.append("%s")
        elif ch == "%":
            out.append("%%")
        else:
            out.append(ch)
    return "".join(out)


class Fila:
    """Fila con el comportamiento de `sqlite3.Row`: por nombre, por posición, `keys()` y
    `dict(fila)`."""
    __slots__ = ("_claves", "_valores", "_indice")

    def __init__(self, claves: Sequence[str], valores: Sequence[Any]):
        self._claves = tuple(claves)
        self._valores = tuple(valores)
        self._indice = {c: i for i, c in enumerate(self._claves)}

    def __getitem__(self, clave):
        if isinstance(clave, (int, slice)):
            return self._valores[clave]
        return self._valores[self._indice[clave]]

    def keys(self) -> list[str]:
        return list(self._claves)

    def __iter__(self):
        return iter(self._valores)

    def __len__(self) -> int:
        return len(self._valores)

    def __eq__(self, otra) -> bool:
        if isinstance(otra, Fila):
            return self._claves == otra._claves and self._valores == otra._valores
        return NotImplemented

    __hash__ = None

    def __repr__(self) -> str:
        return f"Fila({dict(zip(self._claves, self._valores))!r})"


class Cursor:
    """Resultado de `Conexion.execute` con la forma de `sqlite3.Cursor`."""

    def __init__(self, resultado=None, lastrowid: Optional[int] = None, filas: Optional[list] = None):
        self._resultado = resultado
        self._filas = filas
        self.rowcount = resultado.rowcount if resultado is not None else -1
        self.lastrowid = lastrowid

    def _envolver(self, fila) -> Fila:
        return Fila(fila._fields, tuple(fila))

    def fetchone(self) -> Optional[Fila]:
        if self._filas is not None:
            return self._filas.pop(0) if self._filas else None
        if self._resultado is None or not self._resultado.returns_rows:
            return None
        fila = self._resultado.fetchone()
        return self._envolver(fila) if fila is not None else None

    def fetchall(self) -> list[Fila]:
        if self._filas is not None:
            filas, self._filas = self._filas, []
            return filas
        if self._resultado is None or not self._resultado.returns_rows:
            return []
        return [self._envolver(f) for f in self._resultado.fetchall()]

    def __iter__(self):
        return iter(self.fetchall())


class Conexion:
    """Conexión con la forma de `sqlite3.Connection` sobre una `Connection` de SQLAlchemy."""

    def __init__(self, conexion: Connection):
        self._c = conexion
        self._pg = conexion.dialect.name == "postgresql"

    @property
    def sqlalchemy(self) -> Connection:
        return self._c

    def execute(self, sql: str, params: Sequence[Any] = ()) -> Cursor:
        if _es_ddl_heredado(sql):
            logger.debug("DDL heredado ignorado (el esquema es de Alembic): %s", sql.strip()[:60])
            return Cursor()
        params = tuple(params)
        if not self._pg:
            r = self._c.exec_driver_sql(sql, params)
            return Cursor(r, lastrowid=r.lastrowid)
        m = _INSERT.match(sql)
        if m and m.group(1).lower() in TABLAS_CON_ID and not re.search(r"\bRETURNING\b", sql, re.IGNORECASE):
            r = self._c.exec_driver_sql(_a_psycopg(sql.rstrip().rstrip(";"), bool(params)) + " RETURNING id", params)
            fila = r.fetchone()
            cur = Cursor(lastrowid=fila[0] if fila else None, filas=[])
            cur.rowcount = r.rowcount
            return cur
        return Cursor(self._c.exec_driver_sql(_a_psycopg(sql, bool(params)), params))

    def executemany(self, sql: str, lista: Sequence[Sequence[Any]]) -> Cursor:
        lista = [tuple(p) for p in lista]
        if not lista:
            return Cursor()
        return Cursor(self._c.exec_driver_sql(sql if not self._pg else _a_psycopg(sql), lista))

    def executescript(self, script: str) -> None:
        for sentencia in (s.strip() for s in _sin_comentarios(script).split(";")):
            if sentencia:
                self.execute(sentencia)

    def commit(self) -> None:
        self._c.commit()

    def rollback(self) -> None:
        self._c.rollback()

    def close(self) -> None:
        self._c.close()


def _sin_comentarios(script: str) -> str:
    return re.sub(r"--[^\n]*", "", script)


def conectar() -> Conexion:
    """Una conexión nueva (quien la pide hace `commit` y `close`)."""
    return Conexion(motor().connect())


@contextmanager
def transaccion(inmediata: bool = False) -> Iterator[Conexion]:
    """Una transacción: `commit` al salir, `rollback` si hay excepción.

    `inmediata=True` toma el candado de escritura desde el inicio (en SQLite,
    `BEGIN IMMEDIATE`). Es la base del folio atómico (L7); en PostgreSQL el candado lo da el
    `SELECT … FOR UPDATE` de la consulta.
    """
    con = conectar()
    try:
        if inmediata and not con._pg:
            con.sqlalchemy.exec_driver_sql("BEGIN IMMEDIATE")
        yield con
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()
