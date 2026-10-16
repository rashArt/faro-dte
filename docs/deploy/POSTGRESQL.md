# PostgreSQL y SQLite con SQLAlchemy Core + Alembic

> **Estado (2026-10-15): no implementado.** Corresponde a la fase 1 de [`PLAN.md`](PLAN.md) (prioridad).
> Hoy el motor Python usa `sqlite3` directamente (`core/database.py`), sin ORM ni migraciones.
> Go y Rust (`nucleo/`, `nucleo-rs/`) siguen con SQLite: quedan en el roadmap.

## Resultado esperado

- **Mismo código para SQLite y Postgres**, elegido por `DATABASE_URL`:
  - `sqlite:////app/storage/dte_database.db`: local, pruebas, instalaciones chicas.
  - `postgresql+psycopg://faro:…@db:5432/faro`: producción, gestionable desde pgAdmin o DBeaver.
- **Esquema versionado con Alembic** en los dos dialectos. `alembic upgrade head` crea o
  actualiza la base, y `alembic current` dice en qué versión está.
- **El flujo completo pasa en las dos bases**: alta (certificado, negocio, CAF), emisión
  concurrente, idempotencia, envío simulado, consulta pública. Es la misma suite `tests/api/`
  corrida dos veces.
- **El contrato de la API no cambia** por esta fase. Fechas y montos siguen con el mismo formato.

**Sin migración de datos.** El plan anterior traía un script para copiar una base SQLite
existente a Postgres; servía para instalaciones que ya tienen datos. Las instancias de este
fork parten de cero, así que pasa al roadmap.

## Por qué SQLAlchemy Core y no el ORM

| Opción | Qué implica aquí |
|---|---|
| `sqlite3` + `psycopg` a mano | dos caminos de código, pool propio y traducción de SQL propia. Es más frágil. |
| **SQLAlchemy Core** | `Engine` con pool y dialecto, transacciones y `text()` con parámetros con nombre. El SQL escrito a mano se conserva casi igual, y `core/database.py` mantiene su firma, así que la mayoría de las ~110 llamadas no se tocan. |
| SQLAlchemy ORM (modelos) | reescribir cada consulta y cada `dict(row)` del motor. Más riesgo sin beneficio para el objetivo. Se puede adoptar después, módulo por módulo, sobre el mismo `Engine` y las mismas migraciones. |

Alembic usa ese mismo `Engine`. Sus migraciones se escriben con `op.create_table` y quedan
portables entre dialectos.

## Inventario medido (lo que cambia)

| Construcción SQLite | Dónde | Cómo queda |
|---|---|---|
| Placeholders `?` | todas las consultas | La fachada los convierte a parámetros con nombre de `text()` (`:p0, :p1…`). No hay `%` literales en el SQL actual (verificado). |
| `CREATE TABLE IF NOT EXISTS` en 7 módulos (`database`, `apikeys`, `folios_auto`, `keystore`, `negocios`, `rcv`, `resolucion`), 13 tablas | arranque | migración Alembic `0001_esquema_inicial`. Los `init_*` dejan de crear tablas. |
| `ALTER TABLE` condicional con `PRAGMA table_info` | `core/negocios.py:54-57` | queda dentro de `0001`; los cambios futuros son migraciones nuevas |
| `INTEGER PRIMARY KEY AUTOINCREMENT` (10 tablas) | DDL | `Integer, primary_key=True, autoincrement=True`: `SERIAL`/identity en Postgres |
| `BLOB` | `keystore.py:52-53, 90-91` (secretos cifrados) | `LargeBinary` → `BYTEA` |
| `REAL` | `valores` | `Float` |
| `cursor.lastrowid` (8 lugares) | `apikeys.py:60`, `keystore.py:201`, `models.py:55,231,377,429`, `negocios.py:121`, `rvd.py:155` | `INSERT … RETURNING id` mediante `database.insertar()` |
| `executescript` | `database.py`, `rcv.py:171` | lo reemplaza Alembic |
| `PRAGMA journal_mode/foreign_keys` | `database.py`, `models.py:313` | en SQLite, un evento `connect` del `Engine`; en Postgres no aplica |
| **`BEGIN IMMEDIATE`** (folio atómico, L7) | `models.py:294-355` | SQLite: `BEGIN IMMEDIATE`. Postgres: `SELECT … FOR UPDATE` sobre la fila del CAF. Una sola función con dos ramas, cubierta por la prueba de concurrencia. |
| `datetime('now')` | `sii_mipyme.py:1393` | parámetro desde `core/reloj.py` (hoy guarda UTC; el resto del motor guarda hora de Chile) |
| `sqlite3.Row` (acceso por nombre y por índice) | todas; por índice en `negocios.py:180`, `rvd.py:121` | `Row` de SQLAlchemy (`._mapping` por nombre, índice por posición) envuelto para que `dict(row)` siga funcionando |
| `sqlite3.IntegrityError` / `OperationalError` | `models.py:349`, rutas | `sqlalchemy.exc.IntegrityError` → 409 (fase 1 del plan) |
| `ON CONFLICT(…) DO UPDATE … excluded.` (5 lugares) | `folios_auto`, `keystore`, `rcv`, `resolucion`, `rvd` | misma sintaxis en ambos dialectos; los `UNIQUE` existen |
| Afinidad de tipos de SQLite | a revisar | Postgres no compara `TEXT` con número. Lo detecta la suite en Postgres. |
| `DATABASE_PATH` expuesto y `database_url` en el log | `keystore.py:225`, `mipyme.py:70`, `main.py:59` | con Postgres sería un DSN con contraseña: se enmascara y no se devuelve por la API |

`GROUP BY` (`api/routes/dte.py:154`) y `LIKE ?` (`valores.py:63`) ya son válidos en Postgres.

## Pasos

| Paso | Entrega | Verificación |
|---|---|---|
| 1.1 | `core/database.py` sobre `Engine` (SQLite), conversión de `?` a parámetros con nombre, `insertar()`, filas compatibles con `dict()` | `test_dorado` 15/15, `test_simulacion`, `test_mvp`, `tests/api/` sin cambios de resultado |
| 1.2 | Alembic: `alembic.ini`, `migraciones/`, `0001_esquema_inicial` (13 tablas e índices); los `init_*` ya no crean tablas | base vacía + `alembic upgrade head` = mismo esquema que hoy, comparado tabla por tabla |
| 1.3 | Dialecto Postgres: `psycopg[binary]` 3, folio con `FOR UPDATE`, `BYTEA`, `RETURNING` | `tests/api/` con `DATABASE_URL=postgresql+psycopg://…` |
| 1.4 | Compose local con Postgres (abajo) | levantar, `alembic upgrade head`, correr `http/flujos/` y la suite contra la instancia |
| 1.5 | Concurrencia | 50 hilos sobre el mismo CAF: folios únicos, consecutivos y sin huecos, en las dos bases |
| 1.6 | CI | job con servicio `postgres:17` que corre la suite en ambos dialectos |

`tools/esquema_dorado.py` compara el esquema de Python con el de Go. Con Alembic aparece la
tabla `alembic_version`: el gate la ignora, igual que ya ignora `migraciones`.

## Compose local con Postgres (paso 1.4)

```yaml
services:
  faro:
    # … igual que docker-compose.example.yml
    environment:
      - TZ=America/Santiago
      - DATABASE_URL=postgresql+psycopg://faro:${POSTGRES_PASSWORD}@db:5432/faro
    depends_on:
      db: { condition: service_healthy }
  db:
    image: postgres:17
    restart: unless-stopped
    environment:
      - POSTGRES_DB=faro
      - POSTGRES_USER=faro
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
    volumes: [pg-data:/var/lib/postgresql/data]
    ports: ["127.0.0.1:5432:5432"]          # solo local: para pgAdmin/DBeaver del equipo
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U faro -d faro"]
      interval: 10s
  pgadmin:                                   # opcional: docker compose --profile admin up -d
    image: dpage/pgadmin4:8
    profiles: [admin]
    environment:
      - PGADMIN_DEFAULT_EMAIL=admin@example.com
      - PGADMIN_DEFAULT_PASSWORD=${PGADMIN_PASSWORD}
    ports: ["127.0.0.1:5050:80"]
volumes: { pg-data: {} }
```

En un servidor de cliente, la base no publica puerto. Se administra por un túnel SSH o desde
un contenedor en la misma red.

## Roadmap (fuera de este plan)

- Postgres en Go y Rust (`nucleo/almacen`, `nucleo-rs/src/almacen.rs`, ~90 llamadas sobre `rusqlite`).
- Script de migración de datos SQLite → Postgres.
- Fechas como `timestamptz`/`date` (cambia el JSON de la API).
- Réplicas: hace falta un candado de líder (`pg_advisory_lock`) para el RVD y la reposición de folios.
