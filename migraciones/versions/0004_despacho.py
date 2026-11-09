"""Despacho al SII: envío y seguimiento del veredicto por documento.

Revisión: 0004
Anterior: 0003
Fecha: 2026-11-09

Fase 5 de docs/deploy/PLAN.md. Extensión del fork, como `0002` y `0003`: Go y Rust no la tienen
y sus INSERT no nombran las columnas nuevas (toman el valor por defecto).

Columnas nuevas en `dtes` (las escribe `core/servicios/envio.py`):

- `seguimiento`: 1 si Faro envía el documento y sigue su veredicto en segundo plano (emitir con
  `enviar`, o un envío a mano). Lo emitido por otras rutas queda en 0 y el despacho no lo toca.
- `intentos_envio`: envíos fallidos seguidos; al tope (`ENVIO_MAX_INTENTOS`) el documento pasa
  a `envio_fallido`.
- `proxima_accion`: cuándo toca reintentar el envío o volver a consultar el veredicto.
- `tomado_hasta`: un proceso trabaja el documento hasta esa hora; otro no lo toma (sirve igual
  con varios procesos o réplicas, sin candados de la base).
- `enviado_en`, `consultado_en`, `veredicto_en`: cuándo se envió, cuándo se preguntó al SII por
  última vez y cuándo llegó el veredicto.
- `motivo_sii`: glosa del SII, detalle del rechazo o del último error de envío.

Estados nuevos de `dtes.estado` (sin CHECK, como en `0002`): `aceptado_con_reparos`,
`envio_fallido` y `sin_veredicto`. `enviado`, `aceptado` y `rechazado` ya existían.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

DDL_SQLITE = [
    "ALTER TABLE dtes ADD COLUMN seguimiento INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE dtes ADD COLUMN intentos_envio INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE dtes ADD COLUMN proxima_accion TEXT",
    "ALTER TABLE dtes ADD COLUMN tomado_hasta TEXT",
    "ALTER TABLE dtes ADD COLUMN enviado_en TEXT",
    "ALTER TABLE dtes ADD COLUMN consultado_en TEXT",
    "ALTER TABLE dtes ADD COLUMN veredicto_en TEXT",
    "ALTER TABLE dtes ADD COLUMN motivo_sii TEXT",
    "CREATE INDEX idx_dtes_despacho ON dtes(seguimiento, estado, proxima_accion)",
]

COLUMNAS = ["seguimiento", "intentos_envio", "proxima_accion", "tomado_hasta", "enviado_en",
            "consultado_en", "veredicto_en", "motivo_sii"]


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        for sentencia in DDL_SQLITE:
            op.execute(sentencia)
        return
    op.add_column("dtes", sa.Column("seguimiento", sa.BigInteger, nullable=False, server_default=sa.text("0")))
    op.add_column("dtes", sa.Column("intentos_envio", sa.BigInteger, nullable=False, server_default=sa.text("0")))
    for columna in COLUMNAS[2:]:
        op.add_column("dtes", sa.Column(columna, sa.Text))
    op.create_index("idx_dtes_despacho", "dtes", ["seguimiento", "estado", "proxima_accion"])


def downgrade() -> None:
    op.drop_index("idx_dtes_despacho", table_name="dtes")
    for columna in reversed(COLUMNAS):
        op.execute(f"ALTER TABLE dtes DROP COLUMN {columna}")
