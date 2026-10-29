#!/usr/bin/env python3
"""
tools/generar_dbml.py — Escribe docs/deploy/schema.dbml desde las migraciones.

Crea una base SQLite temporal, corre `alembic upgrade head` y la refleja: tablas, columnas,
claves, únicos e índices salen de la base real, no de una copia a mano. Las relaciones se
agregan desde `RELACIONES`: el esquema heredado no declara claves foráneas, así que son
**lógicas** (las respeta el código, no la base).

El `.dbml` se ve en https://dbdiagram.io (pegar el archivo) o en la extensión "DBML" de VS Code.

Uso:
    python tools/generar_dbml.py                 # escribe docs/deploy/schema.dbml
    python tools/generar_dbml.py --comprobar     # sale 1 si el archivo no está al día (CI)
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DESTINO = RAIZ / "docs" / "deploy" / "schema.dbml"

NOTAS = {
    "dtes": "Documentos emitidos: uno por folio (UNIQUE rut_emisor + tipo_dte + folio). Guarda el XML firmado. "
            "estado reservado/error = sin timbre (el folio se reusa); intento nombra la carpeta de archivos.",
    "cafs": "Rangos de folios autorizados por el SII. folio_siguiente es el contador atómico (L7).",
    "audit_log": "Bitácora de acciones (emisión, envío, CAF).",
    "usuarios": "Cuentas de acceso (JWT). cuenta_id en otras tablas apunta aquí.",
    "rvd_envios": "Registro de Ventas Diario de boletas: una fila por emisor, día y secuencia.",
    "valores": "UF, UTM y dólar por fecha (referencia pública del SII).",
    "api_keys": "Claves de API (hoy sin cuenta asociada; la fase 4 las liga al administrador).",
    "folios_gestion": "Estado de la reposición automática de folios por clave (rut y tipo).",
    "certificados": "Certificados .p12 y su contraseña, cifrados con Fernet (L6).",
    "claves_sii": "Claves del portal del SII de cada empresa, cifradas.",
    "negocios": "Empresas emisoras de la cuenta y sus datos de carátula; cert_id es el certificado con que firman.",
    "rcv_documentos": "Registro de Compras y Ventas bajado del SII.",
    "resolucion_cache": "Resolución SII de cada emisor por ambiente (caché de la consulta pública).",
    "emisiones": "Ventas del producto (origen + id_externo): huella del JSON y el documento que las atiende. "
                 "dte_id NULL si el folio de un intento fallido pasó a otra venta (fork, 0002).",
}

# (desde, hacia, nota): relaciones lógicas, sin FOREIGN KEY en la base.
RELACIONES = [
    ("certificados.cuenta_id", "usuarios.id", "cuenta dueña"),
    ("claves_sii.cuenta_id", "usuarios.id", "cuenta dueña"),
    ("negocios.cuenta_id", "usuarios.id", "cuenta dueña"),
    ("negocios.cert_id", "certificados.id", "certificado con que firma"),
    ("dtes.rut_emisor", "negocios.rut", "emisor"),
    ("cafs.rut_emisor", "negocios.rut", "emisor"),
    ("dtes.(rut_emisor, tipo_dte)", "cafs.(rut_emisor, tipo_dte)", "el folio sale del CAF activo"),
    ("rvd_envios.rut_emisor", "negocios.rut", "emisor"),
    ("rcv_documentos.rut_empresa", "negocios.rut", "empresa"),
    ("claves_sii.rut_empresa", "negocios.rut", "empresa"),
    ("resolucion_cache.rut", "negocios.rut", "emisor"),
    ("audit_log.(rut_emisor, tipo_dte, folio)", "dtes.(rut_emisor, tipo_dte, folio)", "documento"),
    ("emisiones.dte_id", "dtes.id", "documento de la venta"),
]


def generar() -> str:
    tmp = Path(tempfile.mkdtemp(prefix="faro-dbml-"))
    os.environ["DATABASE_URL"] = f"sqlite:///{(tmp / 'esquema.db').as_posix()}"
    sys.path.insert(0, str(RAIZ))
    from sqlalchemy import inspect

    from core import datos, migrar
    migrar.actualizar()
    insp = inspect(datos.motor())
    from alembic.script import ScriptDirectory
    cabeza = ScriptDirectory.from_config(migrar.configuracion()).get_current_head()

    out = [
        "// Esquema de la base de Faro (motor Python), generado por tools/generar_dbml.py.",
        f"// Migración: {cabeza}. No editar a mano: regenerar tras cada migración.",
        "// Tipos de SQLite; en PostgreSQL, INTEGER es BIGINT, REAL es DOUBLE PRECISION y BLOB es BYTEA.",
        "// Las relaciones (Ref) son lógicas: el esquema heredado no declara claves foráneas.",
        "",
        'Project faro { database_type: "SQLite / PostgreSQL" }',
        "",
    ]
    for tabla in sorted(t for t in insp.get_table_names() if t not in ("alembic_version", "sqlite_sequence")):
        pk = set(insp.get_pk_constraint(tabla)["constrained_columns"])
        unicos = [u["column_names"] for u in insp.get_unique_constraints(tabla)]
        unicos_simples = {u[0] for u in unicos if len(u) == 1}
        out.append(f"Table {tabla} {{")
        for c in insp.get_columns(tabla):
            atributos = []
            if c["name"] in pk:
                atributos.append("pk")
                if len(pk) == 1 and str(c["type"]).upper() == "INTEGER":
                    atributos.append("increment")
            if not c["nullable"] and c["name"] not in pk:
                atributos.append("not null")
            if c["name"] in unicos_simples:
                atributos.append("unique")
            if c.get("default") is not None:
                atributos.append(f"default: `{c['default']}`")
            sufijo = f" [{', '.join(atributos)}]" if atributos else ""
            out.append(f"  {c['name']} {str(c['type']).lower()}{sufijo}")
        indices = [i for i in insp.get_indexes(tabla) if not i["name"].startswith("sqlite_autoindex")]
        compuestos = [u for u in unicos if len(u) > 1]
        if len(pk) > 1 or indices or compuestos:
            out.append("  indexes {")
            if len(pk) > 1:
                out.append(f"    ({', '.join(sorted(pk))}) [pk]")
            for u in compuestos:
                out.append(f"    ({', '.join(u)}) [unique]")
            for i in indices:
                out.append(f"    ({', '.join(i['column_names'])}) [name: '{i['name']}']")
            out.append("  }")
        if tabla in NOTAS:
            out.append(f"  Note: '{NOTAS[tabla]}'")
        out.append("}")
        out.append("")
    for desde, hacia, nota in RELACIONES:
        out.append(f"Ref: {desde} > {hacia} // {nota}")
    datos.reiniciar_motor()
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--comprobar", action="store_true", help="no escribe; sale 1 si el archivo no está al día")
    a = ap.parse_args()
    texto = generar()
    if a.comprobar:
        actual = DESTINO.read_text(encoding="utf-8") if DESTINO.exists() else ""
        if actual != texto:
            print(f"{DESTINO.relative_to(RAIZ)} no está al día: python tools/generar_dbml.py")
            return 1
        print("schema.dbml al día")
        return 0
    DESTINO.write_text(texto, encoding="utf-8", newline="\n")
    print(f"escrito {DESTINO.relative_to(RAIZ)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
