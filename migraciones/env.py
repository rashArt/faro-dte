"""Entorno de Alembic: usa el mismo `Engine` que el motor (core/datos.py)."""
from __future__ import annotations

from logging.config import fileConfig

from alembic import context

from core import datos

config = context.config
if config.config_file_name and config.cmd_opts is not None:  # solo desde la línea de comandos
    fileConfig(config.config_file_name)

# Sin MetaData: las migraciones se escriben a mano (CONVENCIONES.md §3), no se autogeneran.
target_metadata = None


def correr_offline() -> None:
    context.configure(url=datos.url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def correr_online() -> None:
    with datos.motor().connect() as conexion:
        context.configure(connection=conexion, target_metadata=target_metadata,
                          render_as_batch=conexion.dialect.name == "sqlite")
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    correr_offline()
else:
    correr_online()
