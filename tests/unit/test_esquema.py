"""
Migraciones (Alembic) contra el esquema heredado de upstream (docs/deploy/PLAN.md, fase 1).

- En SQLite, `alembic upgrade 0001` desde cero da **el mismo** `sqlite_master` que el DDL que
  upstream corre al arrancar. Si upstream agrega una tabla o columna y nadie escribe su
  migración, esta prueba falla (CONVENCIONES.md §3). Desde `0002` son extensiones del fork.
- Una base heredada (sin `alembic_version`) se adopta sin perder datos.
- `downgrade` a la base y vuelta a `upgrade` funciona.
- Con PostgreSQL (`FARO_TEST_DATABASE_URL`), su esquema tiene las mismas tablas, columnas,
  nulos, claves, únicos, índices y llaves foráneas que el de SQLite.
- `0005`: el ambiente es una llave foránea y el mismo folio cabe en dos ambientes.
"""
from __future__ import annotations

import re
import sqlite3
import tempfile
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect

from core import datos, migrar

IGNORADAS = {"alembic_version", "sqlite_sequence"}
TABLAS_HEAD = 15   # las 13 heredadas + emisiones (0002) + ambientes (0005)


def _sqlite_master(ruta: Path) -> dict:
    con = sqlite3.connect(ruta)
    out = {}
    for tipo, nombre, sql in con.execute("SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL"):
        if nombre in IGNORADAS or nombre.startswith("sqlite_"):
            continue
        s = re.sub(r"--[^\n]*", "", sql)
        s = re.sub(r"\s+", " ", s).strip().replace("( ", "(").replace(" )", ")").replace(" ,", ",")
        out[(tipo, nombre)] = s.upper()
    con.close()
    return out


@pytest.fixture
def carpeta():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


def _url(ruta: Path) -> str:
    return f"sqlite:///{ruta.as_posix()}"


def test_migraciones_dan_el_mismo_esquema_que_el_ddl_heredado(carpeta):
    heredada, migrada = carpeta / "heredada.db", carpeta / "migrada.db"
    with datos.con_url(_url(heredada)):
        migrar.crear_esquema_heredado()
    with datos.con_url(_url(migrada)):
        command.upgrade(migrar.configuracion(), migrar.REVISION_BASE)
    a, b = _sqlite_master(heredada), _sqlite_master(migrada)
    assert sorted(a) == sorted(b), f"solo heredado: {sorted(set(a) - set(b))}; solo migraciones: {sorted(set(b) - set(a))}"
    distintos = {k: (a[k], b[k]) for k in a if a[k] != b[k]}
    assert not distintos, distintos
    assert len([k for k in a if k[0] == "table"]) == 13


def test_base_heredada_se_adopta_sin_perder_datos(carpeta):
    ruta = carpeta / "vieja.db"
    with datos.con_url(_url(ruta)):
        migrar.crear_esquema_heredado()
        with datos.transaccion() as con:
            con.execute("INSERT INTO usuarios (username, hashed_password, creado_en) VALUES (?,?,?)",
                        ("persona_demo", "x", "2026-10-29T10:00:00"))
        migrar.actualizar()
        with datos.transaccion() as con:
            assert con.execute("SELECT username FROM usuarios").fetchone()["username"] == "persona_demo"
            assert con.execute("SELECT version_num FROM alembic_version").fetchone()[0] == _cabeza()


def test_downgrade_a_la_base_y_upgrade_de_nuevo(carpeta):
    ruta = carpeta / "ida_y_vuelta.db"
    with datos.con_url(_url(ruta)):
        cfg = migrar.configuracion()
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "base")
        assert set(inspect(datos.motor()).get_table_names()) <= IGNORADAS
        command.upgrade(cfg, "head")
        assert len(set(inspect(datos.motor()).get_table_names()) - IGNORADAS) == TABLAS_HEAD


def _cabeza() -> str:
    from alembic.script import ScriptDirectory
    return ScriptDirectory.from_config(migrar.configuracion()).get_current_head()


_FAMILIA = {"INTEGER": "entero", "BIGINT": "entero", "TEXT": "texto", "REAL": "real",
            "DOUBLE_PRECISION": "real", "FLOAT": "real", "BLOB": "bytes", "BYTEA": "bytes"}


def _estructura(url: str) -> dict:
    eng = create_engine(url)
    insp = inspect(eng)
    out = {}
    for tabla in sorted(set(insp.get_table_names()) - IGNORADAS):
        out[tabla] = {
            "columnas": {c["name"]: (_FAMILIA[type(c["type"]).__name__.upper()], c["nullable"])
                         for c in insp.get_columns(tabla)},
            "pk": sorted(insp.get_pk_constraint(tabla)["constrained_columns"]),
            "unicos": sorted(tuple(sorted(u["column_names"])) for u in insp.get_unique_constraints(tabla))
            + sorted(tuple(sorted(i["column_names"])) for i in insp.get_indexes(tabla) if i.get("unique")),
            "indices": sorted(i["name"] for i in insp.get_indexes(tabla) if not i.get("unique")),
            "foraneas": sorted((tuple(f["constrained_columns"]), f["referred_table"], tuple(f["referred_columns"]))
                               for f in insp.get_foreign_keys(tabla)),
        }
    eng.dispose()
    return out


@pytest.mark.skipif(not datos.es_postgres(), reason="la suite corre sobre SQLite (FARO_TEST_DATABASE_URL no es PostgreSQL)")
def test_postgres_tiene_la_misma_estructura_que_sqlite(cliente, carpeta):
    ruta = carpeta / "referencia.db"
    with datos.con_url(_url(ruta)):
        migrar.actualizar()
    sqlite, postgres = _estructura(_url(ruta)), _estructura(datos.url())
    # En SQLite la clave primaria INTEGER acepta nulos en la reflexión; en Postgres no.
    for t in sqlite.values():
        for c in t["pk"]:
            t["columnas"][c] = (t["columnas"][c][0], False)
    # Los únicos sin nombre de SQLite se reflejan como índices autogenerados.
    for t in list(sqlite.values()) + list(postgres.values()):
        t["unicos"] = sorted(set(t["unicos"]))
    assert sqlite == postgres


def test_0005_ambiente_con_llave_foranea_y_folio_por_ambiente(carpeta):
    """Datos de `0004` suben a `0005` con su ambiente; el folio 1 cabe en certificación y en
    producción, y un ambiente que no existe se rechaza."""
    from sqlalchemy.exc import IntegrityError

    ruta = carpeta / "ambientes.db"
    ahora = "2026-11-14T10:00:00"
    with datos.con_url(_url(ruta)):
        cfg = migrar.configuracion()
        command.upgrade(cfg, "0004")
        with datos.transaccion() as con:
            con.execute("INSERT INTO dtes (tipo_dte, folio, rut_emisor, rut_receptor, fecha_emision, monto_total, "
                        "ambiente, creado_en, actualizado_en) VALUES (33, 1, '76111111-6', '66666666-6', "
                        "'2026-11-14', 1190, NULL, ?, ?)", (ahora, ahora))
            con.execute("INSERT INTO cafs (tipo_dte, rut_emisor, folio_desde, folio_hasta, folio_siguiente, "
                        "fecha_autorizacion, caf_xml, creado_en) VALUES (33, '76111111-6', 1, 10, 2, "
                        "'2026-11-01', '<CAF/>', ?)", (ahora,))
            con.execute("INSERT INTO cafs (tipo_dte, rut_emisor, folio_desde, folio_hasta, folio_siguiente, "
                        "fecha_autorizacion, caf_xml, creado_en) VALUES (39, '77777777-7', 1, 10, 2, "
                        "'2026-11-01', '<CAF/>', ?)", (ahora,))
            con.execute("INSERT INTO dtes (tipo_dte, folio, rut_emisor, rut_receptor, fecha_emision, monto_total, "
                        "ambiente, creado_en, actualizado_en) VALUES (39, 1, '77777777-7', '66666666-6', "
                        "'2026-11-14', 1190, 'simulacion', ?, ?)", (ahora, ahora))   # la demo usó ese CAF
            con.execute("INSERT INTO emisiones (origen, id_externo, huella, dte_id, creado_en, actualizado_en) "
                        "VALUES ('apikey:1', 'pedido-1', 'h', 1, ?, ?)", (ahora, ahora))
            con.execute("INSERT INTO folios_gestion (clave, ultima_accion) VALUES ('76111111-6|39', 'repuesto')")
        command.upgrade(cfg, "head")
        with datos.transaccion() as con:
            assert con.execute("SELECT ambiente FROM dtes WHERE id=1").fetchone()[0] == "certificacion"
            assert [f[0] for f in con.execute("SELECT ambiente FROM cafs ORDER BY id").fetchall()]                 == ["certificacion", "simulacion"]
            assert con.execute("SELECT ambiente FROM emisiones").fetchone()[0] == "certificacion"
            assert con.execute("SELECT clave FROM folios_gestion").fetchone()[0] == "76111111-6|39|certificacion"
            # El mismo folio, en producción: otro documento.
            con.execute("INSERT INTO dtes (tipo_dte, folio, rut_emisor, rut_receptor, fecha_emision, monto_total, "
                        "ambiente, creado_en, actualizado_en) VALUES (33, 1, '76111111-6', '66666666-6', "
                        "'2026-11-14', 1190, 'produccion', ?, ?)", (ahora, ahora))
        for ambiente in ("certificacion", "pruebas"):   # repetido en su ambiente; ambiente que no existe
            with pytest.raises(IntegrityError):
                with datos.transaccion() as con:
                    con.execute("INSERT INTO dtes (tipo_dte, folio, rut_emisor, rut_receptor, fecha_emision, "
                                "monto_total, ambiente, creado_en, actualizado_en) VALUES (33, 1, '76111111-6', "
                                "'66666666-6', '2026-11-14', 1190, ?, ?, ?)", (ambiente, ahora, ahora))
