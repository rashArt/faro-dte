"""Esquema inicial: las 13 tablas que el motor creaba al arrancar.

Revisión: 0001
Anterior: ninguna
Fecha: 2026-10-29

SQLite: el DDL heredado de upstream copiado literal (`core/database.py::SCHEMA_SQL`, keystore,
apikeys, negocios con sus columnas agregadas, rcv, resolución y folios), para que el esquema
quede idéntico al de siempre y al del motor Go (`tools/esquema_dorado.py`).

PostgreSQL: las mismas tablas y columnas con tipos de Postgres. Todo `INTEGER` de SQLite (64
bits) pasa a `BIGINT`, `REAL` a `DOUBLE PRECISION` y `BLOB` a `BYTEA`. Fechas y horas siguen
como `TEXT` para no cambiar el JSON de la API (CONVENCIONES.md §2).

Esta migración está congelada: si upstream cambia el esquema, va en una migración nueva.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TABLAS = ("dtes", "cafs", "audit_log", "usuarios", "rvd_envios", "valores", "api_keys",
          "folios_gestion", "certificados", "claves_sii", "negocios", "rcv_documentos",
          "resolucion_cache")

# ---------------------------------------------------------------------------------------------
# SQLite: DDL heredado, literal
# ---------------------------------------------------------------------------------------------
DDL_SQLITE = [
    """CREATE TABLE dtes (
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
)""",
    """CREATE TABLE cafs (
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
)""",
    """CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    accion TEXT NOT NULL,
    tipo_dte INTEGER,
    folio INTEGER,
    rut_emisor TEXT,
    detalle TEXT,
    ip_origen TEXT,
    creado_en TEXT NOT NULL
)""",
    """CREATE TABLE usuarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    hashed_password TEXT NOT NULL,
    nombre_completo TEXT,
    email TEXT,
    activo INTEGER DEFAULT 1,
    creado_en TEXT NOT NULL,
    ultimo_acceso TEXT
)""",
    """CREATE TABLE rvd_envios (
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
)""",
    """CREATE TABLE valores (
    indicador   TEXT NOT NULL,
    fecha       TEXT NOT NULL,
    valor       REAL NOT NULL,
    actualizado TEXT NOT NULL,
    PRIMARY KEY (indicador, fecha)
)""",
    "CREATE INDEX idx_rvd_rut_fecha ON rvd_envios(rut_emisor, fecha)",
    "CREATE INDEX idx_dtes_tipo_folio ON dtes(tipo_dte, folio)",
    "CREATE INDEX idx_dtes_rut_emisor ON dtes(rut_emisor)",
    "CREATE INDEX idx_dtes_estado ON dtes(estado)",
    "CREATE INDEX idx_dtes_fecha ON dtes(fecha_emision)",
    "CREATE INDEX idx_cafs_rut_tipo ON cafs(rut_emisor, tipo_dte)",
    "CREATE INDEX idx_audit_fecha ON audit_log(creado_en)",
    "CREATE INDEX idx_audit_accion ON audit_log(accion)",
    """CREATE TABLE api_keys (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre     TEXT NOT NULL,
    prefijo    TEXT NOT NULL,
    key_hash   TEXT NOT NULL UNIQUE,
    activo     INTEGER NOT NULL DEFAULT 1,
    creado     TEXT NOT NULL,
    ultimo_uso TEXT
)""",
    """CREATE TABLE folios_gestion (
    clave         TEXT PRIMARY KEY,
    ultima_accion TEXT,
    ultimo_ts     TEXT,
    detalle       TEXT
)""",
    """CREATE TABLE certificados (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    cuenta_id     INTEGER NOT NULL,
    rut           TEXT NOT NULL,
    nombre        TEXT,
    alias         TEXT,
    vencimiento   TEXT,
    p12_cifrado   BLOB NOT NULL,
    clave_cifrada BLOB NOT NULL,
    creado        TEXT NOT NULL,
    UNIQUE(cuenta_id, rut)
)""",
    """CREATE TABLE claves_sii (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    cuenta_id            INTEGER NOT NULL,
    rut_empresa          TEXT NOT NULL,
    rut_usuario          TEXT,
    tributaria_cifrada   BLOB,
    certificado_cifrada  BLOB,
    actualizado          TEXT NOT NULL,
    UNIQUE(cuenta_id, rut_empresa)
)""",
    """CREATE TABLE negocios (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    cuenta_id    INTEGER NOT NULL,
    rut          TEXT NOT NULL,
    razon_social TEXT,
    cert_id      INTEGER,
    ambiente     TEXT DEFAULT 'certificacion',
    creado       TEXT NOT NULL,
    UNIQUE(cuenta_id, rut)
)""",
    # Columnas que upstream agrega con ALTER TABLE en core/negocios.py::init_negocios.
    *[f"ALTER TABLE negocios ADD COLUMN {col} {tipo}" for col, tipo in (
        ("estado", "TEXT"), ("estado_etiqueta", "TEXT"), ("usa_sw_propio", "INTEGER"),
        ("estado_ts", "TEXT"), ("software_registrado", "TEXT"), ("giro", "TEXT"),
        ("codigo_actividad", "INTEGER"), ("direccion", "TEXT"), ("comuna", "TEXT"),
        ("ciudad", "TEXT"), ("email", "TEXT"), ("telefono", "TEXT"))],
    """CREATE TABLE rcv_documentos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rut_empresa TEXT NOT NULL,
    tipo_operacion TEXT NOT NULL,
    periodo TEXT NOT NULL,
    tipo_dte INTEGER NOT NULL,
    folio INTEGER NOT NULL,
    fecha_doc TEXT,
    rut_contraparte TEXT DEFAULT '',
    razon_social TEXT DEFAULT '',
    monto_exento INTEGER DEFAULT 0,
    monto_neto INTEGER DEFAULT 0,
    monto_iva INTEGER DEFAULT 0,
    monto_iva_no_rec INTEGER DEFAULT 0,
    monto_iva_uso_comun INTEGER DEFAULT 0,
    monto_total INTEGER DEFAULT 0,
    estado_sii TEXT DEFAULT 'REGISTRO',
    clasificacion TEXT,
    origen TEXT DEFAULT 'rcv',
    creado_en TEXT NOT NULL,
    actualizado_en TEXT NOT NULL,
    UNIQUE(rut_empresa, tipo_operacion, tipo_dte, folio, rut_contraparte)
)""",
    "CREATE INDEX idx_rcv_empresa_periodo ON rcv_documentos(rut_empresa, periodo)",
    "CREATE INDEX idx_rcv_operacion ON rcv_documentos(tipo_operacion)",
    """CREATE TABLE resolucion_cache (
    rut       TEXT NOT NULL,
    ambiente  TEXT NOT NULL,
    fecha_iso TEXT NOT NULL,
    numero    INTEGER NOT NULL,
    ts        TEXT NOT NULL,
    PRIMARY KEY (rut, ambiente)
)""",
]


# ---------------------------------------------------------------------------------------------
# PostgreSQL: mismas tablas, tipos de Postgres
# ---------------------------------------------------------------------------------------------
def _id() -> sa.Column:
    return sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True)


def _ent(nombre: str, *, nulo: bool = True, defecto: str | None = None) -> sa.Column:
    return sa.Column(nombre, sa.BigInteger, nullable=nulo,
                     server_default=sa.text(defecto) if defecto is not None else None)


def _txt(nombre: str, *, nulo: bool = True, defecto: str | None = None, unico: bool = False) -> sa.Column:
    return sa.Column(nombre, sa.Text, nullable=nulo, unique=unico,
                     server_default=sa.text(f"'{defecto}'") if defecto is not None else None)


def _tablas_postgres() -> None:
    op.create_table(
        "dtes", _id(),
        _ent("tipo_dte", nulo=False), _ent("folio", nulo=False),
        _txt("rut_emisor", nulo=False), _txt("rut_receptor", nulo=False), _txt("razon_social_receptor"),
        _txt("fecha_emision", nulo=False),
        _ent("monto_neto", defecto="0"), _ent("monto_exento", defecto="0"), _ent("iva", defecto="0"),
        _ent("monto_total", nulo=False),
        _txt("estado", defecto="generado"), _ent("track_id"), _txt("xml_firmado"), _txt("pdf_path"),
        _txt("ambiente", defecto="certificacion"),
        _txt("creado_en", nulo=False), _txt("actualizado_en", nulo=False),
        sa.UniqueConstraint("rut_emisor", "tipo_dte", "folio", name="uq_dtes_rut_emisor_tipo_dte_folio"))
    op.create_table(
        "cafs", _id(),
        _ent("tipo_dte", nulo=False), _txt("rut_emisor", nulo=False),
        _ent("folio_desde", nulo=False), _ent("folio_hasta", nulo=False), _ent("folio_siguiente", nulo=False),
        _txt("fecha_autorizacion", nulo=False), _txt("caf_xml", nulo=False),
        _ent("activo", defecto="1"), _txt("creado_en", nulo=False),
        sa.UniqueConstraint("rut_emisor", "tipo_dte", "folio_desde", "folio_hasta",
                            name="uq_cafs_rut_emisor_tipo_dte_folio_desde_folio_hasta"))
    op.create_table(
        "audit_log", _id(),
        _txt("accion", nulo=False), _ent("tipo_dte"), _ent("folio"), _txt("rut_emisor"),
        _txt("detalle"), _txt("ip_origen"), _txt("creado_en", nulo=False))
    op.create_table(
        "usuarios", _id(),
        _txt("username", nulo=False, unico=True), _txt("hashed_password", nulo=False),
        _txt("nombre_completo"), _txt("email"), _ent("activo", defecto="1"),
        _txt("creado_en", nulo=False), _txt("ultimo_acceso"))
    op.create_table(
        "rvd_envios", _id(),
        _txt("rut_emisor", nulo=False), _txt("fecha", nulo=False),
        _ent("sec_envio", nulo=False, defecto="1"), _txt("estado", nulo=False, defecto="pendiente"),
        _txt("track_id"), _txt("xml_path"), _txt("detalle"),
        _txt("creado_en", nulo=False), _txt("actualizado_en"),
        sa.UniqueConstraint("rut_emisor", "fecha", "sec_envio", name="uq_rvd_envios_rut_emisor_fecha_sec_envio"))
    op.create_table(
        "valores",
        sa.Column("indicador", sa.Text, primary_key=True), sa.Column("fecha", sa.Text, primary_key=True),
        sa.Column("valor", sa.Float, nullable=False), _txt("actualizado", nulo=False))
    op.create_index("idx_rvd_rut_fecha", "rvd_envios", ["rut_emisor", "fecha"])
    op.create_index("idx_dtes_tipo_folio", "dtes", ["tipo_dte", "folio"])
    op.create_index("idx_dtes_rut_emisor", "dtes", ["rut_emisor"])
    op.create_index("idx_dtes_estado", "dtes", ["estado"])
    op.create_index("idx_dtes_fecha", "dtes", ["fecha_emision"])
    op.create_index("idx_cafs_rut_tipo", "cafs", ["rut_emisor", "tipo_dte"])
    op.create_index("idx_audit_fecha", "audit_log", ["creado_en"])
    op.create_index("idx_audit_accion", "audit_log", ["accion"])
    op.create_table(
        "api_keys", _id(),
        _txt("nombre", nulo=False), _txt("prefijo", nulo=False), _txt("key_hash", nulo=False, unico=True),
        _ent("activo", nulo=False, defecto="1"), _txt("creado", nulo=False), _txt("ultimo_uso"))
    op.create_table(
        "folios_gestion",
        sa.Column("clave", sa.Text, primary_key=True),
        _txt("ultima_accion"), _txt("ultimo_ts"), _txt("detalle"))
    op.create_table(
        "certificados", _id(),
        _ent("cuenta_id", nulo=False), _txt("rut", nulo=False), _txt("nombre"), _txt("alias"), _txt("vencimiento"),
        sa.Column("p12_cifrado", sa.LargeBinary, nullable=False),
        sa.Column("clave_cifrada", sa.LargeBinary, nullable=False),
        _txt("creado", nulo=False),
        sa.UniqueConstraint("cuenta_id", "rut", name="uq_certificados_cuenta_id_rut"))
    op.create_table(
        "claves_sii", _id(),
        _ent("cuenta_id", nulo=False), _txt("rut_empresa", nulo=False), _txt("rut_usuario"),
        sa.Column("tributaria_cifrada", sa.LargeBinary), sa.Column("certificado_cifrada", sa.LargeBinary),
        _txt("actualizado", nulo=False),
        sa.UniqueConstraint("cuenta_id", "rut_empresa", name="uq_claves_sii_cuenta_id_rut_empresa"))
    op.create_table(
        "negocios", _id(),
        _ent("cuenta_id", nulo=False), _txt("rut", nulo=False), _txt("razon_social"), _ent("cert_id"),
        _txt("ambiente", defecto="certificacion"), _txt("creado", nulo=False),
        _txt("estado"), _txt("estado_etiqueta"), _ent("usa_sw_propio"), _txt("estado_ts"),
        _txt("software_registrado"), _txt("giro"), _ent("codigo_actividad"), _txt("direccion"),
        _txt("comuna"), _txt("ciudad"), _txt("email"), _txt("telefono"),
        sa.UniqueConstraint("cuenta_id", "rut", name="uq_negocios_cuenta_id_rut"))
    op.create_table(
        "rcv_documentos", _id(),
        _txt("rut_empresa", nulo=False), _txt("tipo_operacion", nulo=False), _txt("periodo", nulo=False),
        _ent("tipo_dte", nulo=False), _ent("folio", nulo=False), _txt("fecha_doc"),
        _txt("rut_contraparte", defecto=""), _txt("razon_social", defecto=""),
        _ent("monto_exento", defecto="0"), _ent("monto_neto", defecto="0"), _ent("monto_iva", defecto="0"),
        _ent("monto_iva_no_rec", defecto="0"), _ent("monto_iva_uso_comun", defecto="0"),
        _ent("monto_total", defecto="0"),
        _txt("estado_sii", defecto="REGISTRO"), _txt("clasificacion"), _txt("origen", defecto="rcv"),
        _txt("creado_en", nulo=False), _txt("actualizado_en", nulo=False),
        sa.UniqueConstraint("rut_empresa", "tipo_operacion", "tipo_dte", "folio", "rut_contraparte",
                            name="uq_rcv_documentos_documento"))
    op.create_index("idx_rcv_empresa_periodo", "rcv_documentos", ["rut_empresa", "periodo"])
    op.create_index("idx_rcv_operacion", "rcv_documentos", ["tipo_operacion"])
    op.create_table(
        "resolucion_cache",
        sa.Column("rut", sa.Text, primary_key=True), sa.Column("ambiente", sa.Text, primary_key=True),
        _txt("fecha_iso", nulo=False), _ent("numero", nulo=False), _txt("ts", nulo=False))


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        for sentencia in DDL_SQLITE:
            op.execute(sentencia)
    else:
        _tablas_postgres()


def downgrade() -> None:
    for tabla in reversed(TABLAS):
        op.drop_table(tabla)
