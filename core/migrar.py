"""
core/migrar.py — Esquema de la base con Alembic (`migraciones/`).

`actualizar()` deja la base en la última migración (`alembic upgrade head`). La llama
`core.database.init_db()` al arrancar, y en un despliegue se corre también como paso
explícito (`alembic upgrade head`, docs/deploy/README.md).

**Base heredada** (creada por upstream o por este motor antes de Alembic): tiene tablas pero
no `alembic_version`. Se completa con el DDL heredado, como hacía el arranque de siempre, y se
marca en `0001`, que es exactamente ese esquema. Desde ahí siguen las migraciones del fork.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from core import datos

logger = logging.getLogger("dte.migrar")

RAIZ = Path(__file__).resolve().parent.parent
REVISION_BASE = "0001"


def configuracion() -> Config:
    """La configuración de Alembic del repositorio, con la URL de `settings`."""
    cfg = Config(str(RAIZ / "alembic.ini"))
    cfg.set_main_option("script_location", str(RAIZ / "migraciones"))
    cfg.set_main_option("sqlalchemy.url", datos.url().replace("%", "%%"))
    return cfg


def actualizar() -> None:
    """`alembic upgrade head`, adoptando antes una base heredada si hace falta."""
    cfg = configuracion()
    with datos.motor().connect() as con:
        tablas = set(inspect(con).get_table_names())
    if "alembic_version" not in tablas and "dtes" in tablas:
        logger.warning("Base sin alembic_version: se completa con el esquema heredado y se marca %s", REVISION_BASE)
        crear_esquema_heredado()
        command.stamp(cfg, REVISION_BASE)
    command.upgrade(cfg, "head")


def crear_esquema_heredado() -> None:
    """El esquema tal como lo crea upstream al arrancar: `SCHEMA_SQL`, los `init_*` de cada
    módulo (con sus `ALTER TABLE` condicionales) y cualquier `CREATE … IF NOT EXISTS` que
    aparezca en `core/*.py`. Solo SQLite: es el DDL de upstream."""
    from core import apikeys, database, keystore, negocios, rcv
    with datos.ddl_heredado():
        with database.get_db() as con:
            con.executescript(database.SCHEMA_SQL)
        keystore.init_keystore()
        apikeys.init_api_keys()
        negocios.init_negocios()
        rcv.init_rcv_db()
        with database.get_db() as con:
            for ddl in ddl_en_modulos():
                con.execute(ddl)


def ddl_en_modulos() -> list[str]:
    """Todo `CREATE TABLE|INDEX IF NOT EXISTS …;` escrito en `core/*.py` (salvo este módulo)."""
    sentencias = []
    for ruta in sorted((RAIZ / "core").glob("*.py")):
        if ruta.name in ("migrar.py", "datos.py"):
            continue
        texto = ruta.read_text(encoding="utf-8")
        sentencias += re.findall(r"CREATE (?:UNIQUE )?(?:TABLE|INDEX) IF NOT EXISTS[\s\S]*?;", texto)
    return sentencias
