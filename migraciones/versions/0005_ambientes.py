"""Ambientes: tabla `ambientes` y el ambiente de cada CAF, documento, emisión y RVD.

Revisión: 0005
Anterior: 0004
Fecha: 2026-11-14

Fase 7 de docs/deploy/PLAN.md. Extensión del fork: Go y Rust no la tienen.

Certificación y producción son ambientes separados del SII: cada uno entrega sus propios CAF y
numera por su cuenta, así que el folio 1 existe en ambos y son documentos distintos. Hasta
`0004` el ambiente era un texto libre en tres tablas y los CAF no lo tenían.

- `ambientes` (nueva): `codigo` es la llave (`certificacion`, `produccion`, `simulacion`).
  `simulacion` es la demo local (`core/simulacion.py`): nunca sale al SII.
- `ambiente` pasa a ser NOT NULL con llave foránea a `ambientes.codigo` en `dtes`, `negocios` y
  `resolucion_cache`, y se agrega a `cafs`, `emisiones` y `rvd_envios`.
- Los únicos incluyen el ambiente:
    dtes        (rut_emisor, tipo_dte, folio, ambiente)
    cafs        (rut_emisor, tipo_dte, folio_desde, folio_hasta, ambiente)
    emisiones   (origen, id_externo, ambiente)
    rvd_envios  (rut_emisor, fecha, sec_envio, ambiente)
- `folios_gestion.clave` pasa de `rut|tipo` a `rut|tipo|ambiente`.
- `cafs.en_uso`: 1 en el CAF del que salen los folios de su tipo y ambiente. Lo elige el motor
  (el vigente que vence primero) o lo fija el administrador (`PUT /api/v1/caf/{id}/en-uso`).
  Queda en 0 al migrar: la primera emisión lo elige.

Datos existentes: hasta `0004` el ambiente de la instancia lo decidía `SII_AMBIENTE` y
`negocios.ambiente` no se usaba para emitir; desde `0005` manda el de la empresa. Por eso la
empresa, los CAF, los RVD y las claves de folios toman el ambiente actual de la instancia
(`SII_AMBIENTE`); los CAF que usó la demo, `simulacion`; cada emisión, el de su documento.

SQLite no agrega llaves foráneas ni cambia únicos con ALTER TABLE: cada tabla se crea de nuevo
y se copian sus filas. La bajada falla si ya hay el mismo folio en dos ambientes (no cabe en
el esquema anterior).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

AMBIENTES = [
    ("certificacion", "Certificación (pruebas del SII)"),
    ("produccion", "Producción (documentos reales)"),
    ("simulacion", "Simulación (demo local, no sale al SII)"),
]
CODIGOS = [c for c, _ in AMBIENTES]
RUT_DEMO = "77777777-7"   # core/simulacion.py

# (tabla, único anterior, columnas anteriores, único nuevo)
UNICOS = [
    ("dtes", "uq_dtes_rut_emisor_tipo_dte_folio", ["rut_emisor", "tipo_dte", "folio"]),
    ("cafs", "uq_cafs_rut_emisor_tipo_dte_folio_desde_folio_hasta",
     ["rut_emisor", "tipo_dte", "folio_desde", "folio_hasta"]),
    ("emisiones", "uq_emisiones_origen_id_externo", ["origen", "id_externo"]),
    ("rvd_envios", "uq_rvd_envios_rut_emisor_fecha_sec_envio", ["rut_emisor", "fecha", "sec_envio"]),
]
CON_LLAVE = ["dtes", "cafs", "emisiones", "rvd_envios", "negocios", "resolucion_cache"]


def _unico_nuevo(tabla: str) -> str:
    return f"uq_{tabla}_ambiente"


def _llave(tabla: str) -> str:
    return f"fk_{tabla}_ambiente_ambientes"


def _actual() -> str:
    """El ambiente de la instancia al migrar: el que tenían, sin decirlo, sus CAF y RVD."""
    from core.config import settings
    return settings.sii_ambiente if settings.sii_ambiente in CODIGOS else "certificacion"


def _lista(valores) -> str:
    return ", ".join(f"'{v}'" for v in valores)


def _revisar_valores() -> None:
    """Un ambiente desconocido en los datos no cabe en la llave foránea: se avisa antes."""
    con = op.get_bind()
    for tabla in ("dtes", "negocios"):
        filas = con.execute(sa.text(
            f"SELECT DISTINCT ambiente FROM {tabla} "
            f"WHERE ambiente IS NOT NULL AND ambiente NOT IN ({_lista(CODIGOS)})")).fetchall()
        if filas:
            raise RuntimeError(
                f"Migración 0005: {tabla}.ambiente tiene valores desconocidos {[f[0] for f in filas]}. "
                f"Corrígelos a uno de {CODIGOS} y vuelve a migrar.")


# ---------------------------------------------------------------------------------------------
# SQLite: cada tabla se crea de nuevo
# ---------------------------------------------------------------------------------------------
_AMBIENTE_NUEVO = "ambiente TEXT NOT NULL DEFAULT 'certificacion' REFERENCES ambientes(codigo)"

# Cada tabla: su DDL con `{ambiente}` y `{unico}`, sus columnas sin `ambiente` y sus índices.
SQLITE = {
    "dtes": {
        "ddl": """CREATE TABLE dtes (
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
    {ambiente},
    creado_en TEXT NOT NULL,
    actualizado_en TEXT NOT NULL,
    intento INTEGER NOT NULL DEFAULT 0,
    motivo_error TEXT,
    origen TEXT,
    seguimiento INTEGER NOT NULL DEFAULT 0,
    intentos_envio INTEGER NOT NULL DEFAULT 0,
    proxima_accion TEXT,
    tomado_hasta TEXT,
    enviado_en TEXT,
    consultado_en TEXT,
    veredicto_en TEXT,
    motivo_sii TEXT,
    {unico}
)""",
        "columnas": ["id", "tipo_dte", "folio", "rut_emisor", "rut_receptor", "razon_social_receptor",
                     "fecha_emision", "monto_neto", "monto_exento", "iva", "monto_total", "estado",
                     "track_id", "xml_firmado", "pdf_path", "creado_en", "actualizado_en", "intento",
                     "motivo_error", "origen", "seguimiento", "intentos_envio", "proxima_accion",
                     "tomado_hasta", "enviado_en", "consultado_en", "veredicto_en", "motivo_sii"],
        "ambiente_antes": "ambiente TEXT DEFAULT 'certificacion'",
        "indices": ["CREATE INDEX idx_dtes_tipo_folio ON dtes(tipo_dte, folio)",
                    "CREATE INDEX idx_dtes_rut_emisor ON dtes(rut_emisor)",
                    "CREATE INDEX idx_dtes_estado ON dtes(estado)",
                    "CREATE INDEX idx_dtes_fecha ON dtes(fecha_emision)",
                    "CREATE INDEX idx_dtes_despacho ON dtes(seguimiento, estado, proxima_accion)"],
    },
    "cafs": {
        "ddl": """CREATE TABLE cafs (
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
    {ambiente}
    {unico}
)""",
        "columnas": ["id", "tipo_dte", "rut_emisor", "folio_desde", "folio_hasta", "folio_siguiente",
                     "fecha_autorizacion", "caf_xml", "activo", "creado_en"],
        "ambiente_antes": None,
        "ambiente_nuevo": "en_uso INTEGER NOT NULL DEFAULT 0,\n    " + _AMBIENTE_NUEVO,
        "indices": ["CREATE INDEX idx_cafs_rut_tipo ON cafs(rut_emisor, tipo_dte)"],
    },
    "emisiones": {
        "ddl": """CREATE TABLE emisiones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    origen TEXT NOT NULL,
    id_externo TEXT NOT NULL,
    huella TEXT NOT NULL,
    dte_id INTEGER,
    creado_en TEXT NOT NULL,
    actualizado_en TEXT NOT NULL,
    {ambiente}
    {unico}
)""",
        "columnas": ["id", "origen", "id_externo", "huella", "dte_id", "creado_en", "actualizado_en"],
        "ambiente_antes": None,
        "indices": ["CREATE INDEX ix_emisiones_dte_id ON emisiones (dte_id)"],
    },
    "rvd_envios": {
        "ddl": """CREATE TABLE rvd_envios (
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
    {ambiente}
    {unico}
)""",
        "columnas": ["id", "rut_emisor", "fecha", "sec_envio", "estado", "track_id", "xml_path",
                     "detalle", "creado_en", "actualizado_en"],
        "ambiente_antes": None,
        "indices": ["CREATE INDEX idx_rvd_rut_fecha ON rvd_envios(rut_emisor, fecha)"],
    },
    "negocios": {
        "ddl": """CREATE TABLE negocios (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    cuenta_id    INTEGER NOT NULL,
    rut          TEXT NOT NULL,
    razon_social TEXT,
    cert_id      INTEGER,
    {ambiente},
    creado       TEXT NOT NULL,
    estado TEXT,
    estado_etiqueta TEXT,
    usa_sw_propio INTEGER,
    estado_ts TEXT,
    software_registrado TEXT,
    giro TEXT,
    codigo_actividad INTEGER,
    direccion TEXT,
    comuna TEXT,
    ciudad TEXT,
    email TEXT,
    telefono TEXT,
    UNIQUE(cuenta_id, rut)
)""",
        "columnas": ["id", "cuenta_id", "rut", "razon_social", "cert_id", "creado", "estado",
                     "estado_etiqueta", "usa_sw_propio", "estado_ts", "software_registrado", "giro",
                     "codigo_actividad", "direccion", "comuna", "ciudad", "email", "telefono"],
        "ambiente_antes": "ambiente     TEXT DEFAULT 'certificacion'",
        "indices": [],
    },
    "resolucion_cache": {
        "ddl": """CREATE TABLE resolucion_cache (
    rut       TEXT NOT NULL,
    {ambiente},
    fecha_iso TEXT NOT NULL,
    numero    INTEGER NOT NULL,
    ts        TEXT NOT NULL,
    PRIMARY KEY (rut, ambiente)
)""",
        "columnas": ["rut", "fecha_iso", "numero", "ts"],
        "ambiente_antes": "ambiente  TEXT NOT NULL",
        "ambiente_nuevo": "ambiente  TEXT NOT NULL REFERENCES ambientes(codigo)",
        "indices": [],
    },
}


def _ddl_sqlite(tabla: str, *, nuevo: bool) -> str:
    """El DDL de `tabla` con el ambiente y el único de `0005` (`nuevo`) o los de `0004`."""
    t = SQLITE[tabla]
    anterior = next((u for u in UNICOS if u[0] == tabla), None)
    if nuevo:
        ambiente = t.get("ambiente_nuevo", _AMBIENTE_NUEVO)
        unico = (f"CONSTRAINT {_unico_nuevo(tabla)} UNIQUE ({', '.join(anterior[2] + ['ambiente'])})"
                 if anterior else "")
    else:
        ambiente = t["ambiente_antes"] or ""
        unico = f"CONSTRAINT {anterior[1]} UNIQUE ({', '.join(anterior[2])})" if anterior else ""
    # Las tablas que no tenían `ambiente` lo llevan al final: `{ambiente}\n    {unico}`.
    partes = [p for p in (ambiente, unico) if p]
    ddl = t["ddl"]
    if "{ambiente}\n    {unico}" in ddl:
        return ddl.replace("{ambiente}\n    {unico}", ",\n    ".join(partes))
    return ddl.replace("{ambiente}", ambiente).replace("{unico}", unico)


def _reconstruir(tabla: str, *, nuevo: bool, ambiente_sql: str | None) -> None:
    """Crea `tabla` de nuevo con el DDL pedido y copia sus filas. `ambiente_sql` es la expresión
    que llena `ambiente` (None: la tabla anterior a `0005` no tiene la columna)."""
    con = op.get_bind()
    t = SQLITE[tabla]
    secuencia = con.execute(sa.text("SELECT seq FROM sqlite_sequence WHERE name = :t"), {"t": tabla}).fetchone() \
        if "AUTOINCREMENT" in t["ddl"] else None
    temporal = f"{tabla}__0005"
    op.execute(_ddl_sqlite(tabla, nuevo=nuevo).replace(f"CREATE TABLE {tabla} ", f"CREATE TABLE {temporal} ", 1))
    columnas = list(t["columnas"])
    origen = list(columnas)
    if ambiente_sql is not None:
        columnas.append("ambiente")
        origen.append(ambiente_sql)
    op.execute(f"INSERT INTO {temporal} ({', '.join(columnas)}) SELECT {', '.join(origen)} FROM {tabla}")
    op.execute(f"DROP TABLE {tabla}")
    op.execute(f"ALTER TABLE {temporal} RENAME TO {tabla}")
    for indice in t["indices"]:
        op.execute(indice)
    if secuencia is not None:   # el próximo id no baja aunque se hayan borrado las últimas filas
        con.execute(sa.text("UPDATE sqlite_sequence SET seq = :s WHERE name = :t AND seq < :s"),
                    {"s": secuencia[0], "t": tabla})


def _ambiente_caf(actual: str) -> str:
    """Los CAF de la demo son los de su RUT con documentos simulados en su rango. Un CAF ficticio
    del mismo RUT cargado para probar la API (sin documentos simulados) sigue en el ambiente actual."""
    return (f"CASE WHEN rut_emisor = '{RUT_DEMO}' AND EXISTS (SELECT 1 FROM dtes d "
            "WHERE d.rut_emisor = cafs.rut_emisor AND d.tipo_dte = cafs.tipo_dte AND d.ambiente = 'simulacion' "
            f"AND d.folio BETWEEN cafs.folio_desde AND cafs.folio_hasta) THEN 'simulacion' ELSE '{actual}' END")


def _ambiente_emision(actual: str) -> str:
    return f"COALESCE((SELECT d.ambiente FROM dtes d WHERE d.id = emisiones.dte_id), '{actual}')"


def _claves_folios_subir(actual: str) -> None:
    op.execute(f"UPDATE folios_gestion SET clave = clave || '|{actual}' "
               "WHERE length(clave) - length(replace(clave, '|', '')) = 1")


def _claves_folios_bajar() -> None:
    for codigo in CODIGOS:
        op.execute(f"DELETE FROM folios_gestion WHERE clave LIKE '%|{codigo}' AND EXISTS ("
                   "SELECT 1 FROM folios_gestion otra WHERE otra.clave = "
                   f"substr(folios_gestion.clave, 1, length(folios_gestion.clave) - {len(codigo) + 1}))")
        op.execute(f"UPDATE folios_gestion SET clave = substr(clave, 1, length(clave) - {len(codigo) + 1}) "
                   f"WHERE clave LIKE '%|{codigo}'")


def _crear_ambientes() -> None:
    tabla = op.create_table(
        "ambientes",
        sa.Column("codigo", sa.Text, primary_key=True),
        sa.Column("nombre", sa.Text, nullable=False))
    op.bulk_insert(tabla, [{"codigo": c, "nombre": n} for c, n in AMBIENTES])


def _subir_sqlite(actual: str) -> None:
    op.execute("CREATE TABLE ambientes (\n    codigo TEXT NOT NULL PRIMARY KEY,\n    nombre TEXT NOT NULL\n)")
    for codigo, nombre in AMBIENTES:
        op.execute(sa.text("INSERT INTO ambientes (codigo, nombre) VALUES (:c, :n)").bindparams(c=codigo, n=nombre))
    op.execute(f"DELETE FROM resolucion_cache WHERE ambiente NOT IN ({_lista(CODIGOS)})")
    _reconstruir("dtes", nuevo=True, ambiente_sql="COALESCE(ambiente, 'certificacion')")
    _reconstruir("cafs", nuevo=True, ambiente_sql=_ambiente_caf(actual))
    _reconstruir("emisiones", nuevo=True, ambiente_sql=_ambiente_emision(actual))
    _reconstruir("rvd_envios", nuevo=True, ambiente_sql=f"'{actual}'")
    _reconstruir("negocios", nuevo=True, ambiente_sql=f"'{actual}'")
    _reconstruir("resolucion_cache", nuevo=True, ambiente_sql="ambiente")


def _subir_postgres(actual: str) -> None:
    _crear_ambientes()
    op.execute(f"DELETE FROM resolucion_cache WHERE ambiente NOT IN ({_lista(CODIGOS)})")
    op.execute("UPDATE dtes SET ambiente = 'certificacion' WHERE ambiente IS NULL")
    op.execute(f"UPDATE negocios SET ambiente = '{actual}'")
    for tabla in ("dtes", "negocios"):
        op.alter_column(tabla, "ambiente", nullable=False)
    for tabla in ("cafs", "emisiones", "rvd_envios"):
        op.add_column(tabla, sa.Column("ambiente", sa.Text, nullable=False,
                                       server_default=sa.text("'certificacion'")))
    op.add_column("cafs", sa.Column("en_uso", sa.BigInteger, nullable=False, server_default=sa.text("0")))
    op.execute(f"UPDATE cafs SET ambiente = {_ambiente_caf(actual)}")
    op.execute(f"UPDATE emisiones SET ambiente = {_ambiente_emision(actual)}")
    op.execute(f"UPDATE rvd_envios SET ambiente = '{actual}'")
    for tabla, anterior, columnas in UNICOS:
        op.drop_constraint(anterior, tabla, type_="unique")
        op.create_unique_constraint(_unico_nuevo(tabla), tabla, columnas + ["ambiente"])
    for tabla in CON_LLAVE:
        op.create_foreign_key(_llave(tabla), tabla, "ambientes", ["ambiente"], ["codigo"])


def upgrade() -> None:
    _revisar_valores()
    actual = _actual()
    if op.get_bind().dialect.name == "sqlite":
        _subir_sqlite(actual)
    else:
        _subir_postgres(actual)
    _claves_folios_subir(actual)


def downgrade() -> None:
    _claves_folios_bajar()
    if op.get_bind().dialect.name == "sqlite":
        _reconstruir("resolucion_cache", nuevo=False, ambiente_sql="ambiente")
        _reconstruir("negocios", nuevo=False, ambiente_sql="ambiente")
        for tabla in ("rvd_envios", "emisiones", "cafs"):
            _reconstruir(tabla, nuevo=False, ambiente_sql=None)
        _reconstruir("dtes", nuevo=False, ambiente_sql="ambiente")
        op.execute("DROP TABLE ambientes")
        return
    for tabla in reversed(CON_LLAVE):
        op.drop_constraint(_llave(tabla), tabla, type_="foreignkey")
    for tabla, anterior, columnas in UNICOS:
        op.drop_constraint(_unico_nuevo(tabla), tabla, type_="unique")
        op.create_unique_constraint(anterior, tabla, columnas)
    for tabla in ("rvd_envios", "emisiones", "cafs"):
        op.drop_column(tabla, "ambiente")
    op.drop_column("cafs", "en_uso")
    for tabla in ("dtes", "negocios"):
        op.alter_column(tabla, "ambiente", nullable=True)
    op.drop_table("ambientes")
