# PostgreSQL y SQLite con SQLAlchemy Core + Alembic

> **Estado (2026-10-29): implementado** (fase 1 de [`PLAN.md`](PLAN.md)). El motor Python corre
> sobre SQLite o PostgreSQL según `DATABASE_URL`, y el esquema lo manejan las migraciones de
> Alembic. Go y Rust (`nucleo/`, `nucleo-rs/`) siguen con SQLite: quedan en el roadmap.
> Tablas y relaciones: [`schema.dbml`](schema.dbml). Reglas: [`CONVENCIONES.md`](CONVENCIONES.md).

## Cómo se usa

| Base | `DATABASE_URL` | Para qué |
|---|---|---|
| SQLite | `sqlite:////app/storage/dte_database.db` | local, pruebas, instalaciones chicas |
| PostgreSQL | `postgresql+psycopg://faro:<clave>@db:5432/faro` | producción, gestionable desde pgAdmin o DBeaver |

```bash
alembic upgrade head        # crea o actualiza el esquema (también corre al arrancar el motor)
alembic current             # en qué migración está la base
alembic downgrade -1        # deshace la última (probado: base → head → base → head)
```

Una base creada antes de Alembic (por upstream o por este motor) no tiene `alembic_version`. Al
arrancar se completa con el DDL de siempre y se marca en `0001`, sin tocar los datos
(`core/migrar.py`). Desde ahí siguen las migraciones del fork.

**Sin migración de datos SQLite → Postgres.** Las instancias del fork parten de cero; el script
de copia queda en el roadmap.

## Cómo está armado

| Pieza | Qué hace |
|---|---|
| `core/datos.py` | `Engine` de SQLAlchemy. SQLite: sin pool (una conexión por uso, como `sqlite3.connect`), WAL y claves foráneas al conectar, y el manejo de transacciones del propio `sqlite3`. PostgreSQL: pool con `pre_ping`. `Conexion` y `Cursor` imitan `sqlite3` (`execute`, `executemany`, `executescript`, `fetchone`, `fetchall`, `rowcount`, `lastrowid`) y `Fila` imita `sqlite3.Row` (por nombre, por posición y `dict(fila)`) |
| `core/database.py` | la misma fachada de siempre (`get_db`, `obtenerUno`, `obtener_todos`, `ejecutar`), ahora sobre `core/datos.py`. `init_db()` corre las migraciones |
| `core/migrar.py` | `alembic upgrade head` y la adopción de bases heredadas |
| `migraciones/`, `alembic.ini` | las migraciones. `0001_esquema_inicial`: en SQLite, el DDL heredado copiado literal; en PostgreSQL, las mismas tablas con tipos de Postgres |
| `tools/generar_dbml.py` | escribe [`schema.dbml`](schema.dbml) reflejando una base migrada; CI exige que esté al día |

### Lo que cambió respecto de `sqlite3` a mano

| Construcción | Cómo queda |
|---|---|
| Placeholders `?` | SQLite los recibe tal cual (`exec_driver_sql`); en PostgreSQL se traducen a `%s` fuera de literales y `%` literal a `%%`. Las ~110 consultas no cambian |
| `CREATE TABLE IF NOT EXISTS`, `ALTER TABLE` y `PRAGMA table_info` que los módulos corren al vuelo (7 módulos, 13 tablas) | `core/datos.py` los ignora: el esquema es de Alembic. `tests/unit/test_esquema.py` construye el esquema heredado y exige que sea **idéntico** (`sqlite_master`) al de las migraciones; si upstream agrega una tabla o columna sin su migración, la suite falla |
| `INTEGER PRIMARY KEY AUTOINCREMENT` | Postgres: `BIGINT` con identidad |
| `INTEGER` en general | Postgres: `BIGINT` (en SQLite un entero es de 64 bits; montos y TrackID no caben siempre en 32) |
| `REAL`, `BLOB` | `DOUBLE PRECISION`, `BYTEA` |
| `cursor.lastrowid` (8 lugares) | en PostgreSQL el `INSERT` a una tabla con `id` lleva `RETURNING id` automáticamente; el código no cambia |
| `BEGIN IMMEDIATE` del folio (L7) | `datos.transaccion(inmediata=True)`: en SQLite `BEGIN IMMEDIATE`, en PostgreSQL `SELECT … FOR UPDATE` sobre la fila del CAF. Gate: 50 hilos sobre el mismo CAF dan folios 1..50 sin huecos ni repetidos, en las dos bases |
| `datetime('now')` (`core/sii_mipyme.py`) | parámetro con `datetime.now().isoformat()`, como el resto de `dtes` |
| `DATABASE_PATH` | sigue existiendo para SQLite (lo usan las rutas de la app de escritorio, que comparten el archivo con el motor Go); con PostgreSQL es `None` y esas rutas devuelven `database_path: null`. Si un script reasigna `DATABASE_PATH` a un archivo temporal (lo hacen varios scripts de upstream), la conexión lo sigue |
| `database_url` en el log de arranque | sin contraseña (`datos.url_segura()`) |
| `ON CONFLICT … DO UPDATE … excluded.`, `GROUP BY`, `LIKE ?`, `substr`, `BETWEEN` | iguales en las dos bases |

## Verificación (2026-10-29)

| Prueba | SQLite | PostgreSQL 17 |
|---|---|---|
| Suite pytest (`tests/unit`, `tests/api`) | 7 verdes, 1 omitida (la de Postgres), 7 `xfail` de la fase 2 | 8 verdes, 7 `xfail` |
| Migraciones = DDL heredado (`sqlite_master` idéntico) | ✅ | — |
| Misma estructura que SQLite (tablas, columnas, nulos, claves, únicos, índices) | — | ✅ |
| `downgrade` a la base y `upgrade` de nuevo; adopción de una base heredada con datos | ✅ | — |
| Folio atómico, 50 hilos | ✅ | ✅ |
| Scripts históricos en la imagen (`test_dorado` 15/15, `test_simulacion`, `test_mvp`, `test_mipyme`, `test_robustez`, `test_claves_sii`, `test_rcv`) | ✅ | — |
| `tools/sondeo_integridad.py` contra la imagen | mismos 8 hallazgos que antes del cambio; 20 emisiones simultáneas con folios 1..20 | — |
| Compose con Postgres (fase 1; hoy `docker-compose.prod.yml`): `alembic upgrade head` como paso explícito, 10 emisiones simultáneas por HTTP (folios 1..10), listado, PDF, API key, simulación | — | ✅ |

```bash
# La suite contra un Postgres desechable (se vacía entero al empezar):
FARO_TEST_DATABASE_URL=postgresql+psycopg://faro:<clave>@127.0.0.1:5432/faro_pruebas \
FARO_TEST_PG_DESECHABLE=1 .venv/bin/python -m pytest
```

## Compose con PostgreSQL

- **Local:** [`docker-compose.dev.yml`](../../docker-compose.dev.yml) usa el servicio `db`
  (`postgres:17`, publicado solo en `127.0.0.1:5432`). SQLite queda comentado ahí mismo.
- **Un cliente:** [`docker-compose.prod.yml`](../../docker-compose.prod.yml) (fase 6): la base va
  en una red interna sin puertos ni salida a Internet, y `migrar` corre `alembic upgrade head`
  antes de que arranque Faro. Respaldo con `pg_dump` en [`tools/respaldo.sh`](../../tools/respaldo.sh).
  Se administra con `docker compose exec db psql -U faro faro` o por un túnel SSH.
  Ver [`README.md`](README.md).

## Roadmap (fuera de este plan)

- Postgres en Go y Rust (`nucleo/almacen`, `nucleo-rs/src/almacen.rs`, ~90 llamadas sobre `rusqlite`).
- Script de migración de datos SQLite → Postgres.
- Fechas como `timestamptz`/`date` (cambia el JSON de la API).
- Réplicas: hace falta un candado de líder (`pg_advisory_lock`) para el RVD y la reposición de folios.
- Claves foráneas reales: hoy las relaciones son lógicas ([`schema.dbml`](schema.dbml)).
