"""Emisión con `id_externo`: tabla `emisiones` y columnas `intento` y `motivo_error` en `dtes`.

Revisión: 0002
Anterior: 0001
Fecha: 2026-10-29

Fase 2 de docs/deploy/PLAN.md ("Política de folios"). Extensión del fork: el motor Go y el
Rust no la tienen (`tools/esquema_dorado.py` compara contra `0001`). Sus INSERT en `dtes` no
nombran las columnas nuevas y toman el valor por defecto.

- `emisiones`: una fila por venta del producto (`origen` + `id_externo`). Guarda la huella del
  JSON (reintento o corrección) y el documento que la atiende. `dte_id` queda NULL si el folio
  de un intento fallido pasó a otra venta.
- `dtes.intento`: número del intento de generación sobre la fila. Nombra la carpeta de sus
  archivos (`storage/emisiones/<dte_id>/v<intento>/`), así ninguno se sobrescribe, y protege
  la fila si su reserva venció mientras se generaba.
- `dtes.motivo_error`: por qué falló el último intento (estado `error`).

Estados nuevos de `dtes.estado` (sin CHECK: upstream escribe otros): `reservado` (folio tomado,
sin timbre todavía) y `error` (falló antes de quedar timbrado; el folio vuelve a usarse).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

DDL_SQLITE = [
    """CREATE TABLE emisiones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    origen TEXT NOT NULL,
    id_externo TEXT NOT NULL,
    huella TEXT NOT NULL,
    dte_id INTEGER,
    creado_en TEXT NOT NULL,
    actualizado_en TEXT NOT NULL,
    CONSTRAINT uq_emisiones_origen_id_externo UNIQUE (origen, id_externo)
)""",
    "CREATE INDEX ix_emisiones_dte_id ON emisiones (dte_id)",
    "ALTER TABLE dtes ADD COLUMN intento INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE dtes ADD COLUMN motivo_error TEXT",
]


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        for sentencia in DDL_SQLITE:
            op.execute(sentencia)
        return
    op.create_table(
        "emisiones",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("origen", sa.Text, nullable=False),
        sa.Column("id_externo", sa.Text, nullable=False),
        sa.Column("huella", sa.Text, nullable=False),
        sa.Column("dte_id", sa.BigInteger),
        sa.Column("creado_en", sa.Text, nullable=False),
        sa.Column("actualizado_en", sa.Text, nullable=False),
        sa.UniqueConstraint("origen", "id_externo", name="uq_emisiones_origen_id_externo"))
    op.create_index("ix_emisiones_dte_id", "emisiones", ["dte_id"])
    op.add_column("dtes", sa.Column("intento", sa.BigInteger, nullable=False, server_default=sa.text("0")))
    op.add_column("dtes", sa.Column("motivo_error", sa.Text))


def downgrade() -> None:
    # SQLite ≥ 3.35 borra columnas con ALTER TABLE: no hace falta recrear la tabla.
    op.execute("ALTER TABLE dtes DROP COLUMN motivo_error")
    op.execute("ALTER TABLE dtes DROP COLUMN intento")
    op.drop_index("ix_emisiones_dte_id", table_name="emisiones")
    op.drop_table("emisiones")
