"""Roles, tokens renovables, auditoría con actor y dueño de cada documento.

Revisión: 0003
Anterior: 0002
Fecha: 2026-10-30

Fase 4 de docs/deploy/PLAN.md. Extensión del fork, como `0002`: Go y Rust no la tienen y sus
INSERT no nombran las columnas nuevas (toman el valor por defecto).

- `api_keys.rol`: hoy solo `integracion` (los tokens de los sistemas que llaman a la API). Los
  permisos de cada rol están en `core/autorizacion.py`. Los tokens existentes quedan `integracion`.
- `api_keys.creado_por`: el usuario que creó el token. `api_keys.renovado`: última renovación
  (renovar cambia el secreto y conserva el `id`, así el `id_externo` sigue siendo del mismo dueño).
- `audit_log.actor` (`usuario:<id>` o `apikey:<id>`), `metodo`, `ruta`, `estado_http`: quién hizo
  qué. Nunca el cuerpo de la petición.
- `usuarios.clave_cambiada_en`: los JWT emitidos antes de esa fecha dejan de valer.
- `dtes.origen`: quién emitió el documento por `POST /dte/emitir` (mismo formato que `actor`). Un
  token ve solo los suyos. NULL en lo emitido por otras rutas o antes de esta revisión.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

DDL_SQLITE = [
    "ALTER TABLE api_keys ADD COLUMN rol TEXT NOT NULL DEFAULT 'integracion'",
    "ALTER TABLE api_keys ADD COLUMN creado_por INTEGER",
    "ALTER TABLE api_keys ADD COLUMN renovado TEXT",
    "ALTER TABLE audit_log ADD COLUMN actor TEXT",
    "ALTER TABLE audit_log ADD COLUMN metodo TEXT",
    "ALTER TABLE audit_log ADD COLUMN ruta TEXT",
    "ALTER TABLE audit_log ADD COLUMN estado_http INTEGER",
    "CREATE INDEX idx_audit_actor ON audit_log(actor)",
    "ALTER TABLE usuarios ADD COLUMN clave_cambiada_en TEXT",
    "ALTER TABLE dtes ADD COLUMN origen TEXT",
]

# (tabla, columna) en el orden en que se agregan; la bajada las quita al revés.
COLUMNAS = [("api_keys", "rol"), ("api_keys", "creado_por"), ("api_keys", "renovado"),
            ("audit_log", "actor"), ("audit_log", "metodo"), ("audit_log", "ruta"),
            ("audit_log", "estado_http"), ("usuarios", "clave_cambiada_en"), ("dtes", "origen")]


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        for sentencia in DDL_SQLITE:
            op.execute(sentencia)
        return
    op.add_column("api_keys", sa.Column("rol", sa.Text, nullable=False, server_default=sa.text("'integracion'")))
    op.add_column("api_keys", sa.Column("creado_por", sa.BigInteger))
    op.add_column("api_keys", sa.Column("renovado", sa.Text))
    op.add_column("audit_log", sa.Column("actor", sa.Text))
    op.add_column("audit_log", sa.Column("metodo", sa.Text))
    op.add_column("audit_log", sa.Column("ruta", sa.Text))
    op.add_column("audit_log", sa.Column("estado_http", sa.BigInteger))
    op.create_index("idx_audit_actor", "audit_log", ["actor"])
    op.add_column("usuarios", sa.Column("clave_cambiada_en", sa.Text))
    op.add_column("dtes", sa.Column("origen", sa.Text))


def downgrade() -> None:
    op.drop_index("idx_audit_actor", table_name="audit_log")
    for tabla, columna in reversed(COLUMNAS):
        op.execute(f"ALTER TABLE {tabla} DROP COLUMN {columna}")
