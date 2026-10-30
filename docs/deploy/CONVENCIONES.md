# Convenciones del fork

> **Estado (2026-10-29): vigentes** para todo lo que escriba el fork (rama `servidor-produccion`).
> No reemplazan [`CONSTITUCION.md`](../CONSTITUCION.md): la concretan para base de datos,
> migraciones y API. Lo existente **no se renombra**; estas reglas aplican a lo nuevo y a lo que
> se toca.

## 1. Idioma

Decidido: **español en todo** (ley L13). Es lo que ya usa el proyecto en tablas, columnas, JSON y
código, y es lo que mantiene limpio el traer cambios de upstream.

| Qué | Idioma | Ejemplo |
|---|---|---|
| Tablas, columnas, índices, migraciones | español, `snake_case` | `emisiones.id_externo` |
| Campos JSON de la API (pedido y respuesta) | español, `snake_case` | `{"id_externo": "pedido-1042"}` |
| Módulos, funciones, variables, clases | español (`snake_case`; clases en `PascalCase`) | `reservar_folio()`, `EmitirDocumento` |
| Comentarios, docstrings, logs, commits, docs | español | |
| Textos que ve una persona (`mensaje`) | español neutro, sin jerga (P4) | "El folio ya fue usado" |

Excepciones, siempre por nombre fijo de afuera y nunca por gusto:

- términos de un estándar o de una librería: `username`, `hashed_password`, `access_token`,
  `Authorization`, `track_id`;
- etiquetas del XML del SII: se escriben como las define el XSD (`RUTEmisor`, `TED`, `GiroRecep`);
- lo que ya existe en inglés (`audit_log`, `username`) se queda como está.

## 2. Base de datos

| Elemento | Regla |
|---|---|
| Tablas | plural en español: `dtes`, `cafs`, `negocios`, `emisiones` |
| Clave primaria | `id`, entero autoincremental |
| Clave foránea | `<entidad en singular>_id`: `dte_id`, `cert_id`, `cuenta_id` |
| Marcas de tiempo | `creado_en`, `actualizado_en`: `TEXT` ISO 8601 en hora de Chile, siempre desde `core/reloj.py` (nunca `datetime.now()` ni `datetime('now')`). Igual en Postgres, para no cambiar el JSON; `timestamptz` queda en el roadmap |
| Fechas | `fecha_*`: `TEXT` `AAAA-MM-DD` |
| Montos | `INTEGER` en pesos, sin decimales: `monto_total`, `iva` |
| Booleanos | `INTEGER` 0/1 en los dos dialectos (`activo`). La API los sigue entregando igual |
| RUT | `TEXT` `12345678-5`: sin puntos, con guion, DV en mayúscula |
| Estados | `estado`, `TEXT` en minúsculas `snake_case`, de un conjunto cerrado definido como constantes en el código y con `CHECK` en las tablas nuevas: `reservado`, `error`, `generado`, `enviado`, `aceptado`, `rechazado` |
| Secretos | `LargeBinary` cifrado con Fernet (L6). Nunca en claro |
| Restricciones e índices | nombres por convención de `MetaData` de SQLAlchemy: `pk_<tabla>`, `fk_<tabla>_<columna>_<tabla destino>`, `uq_<tabla>_<columnas>`, `ix_<tabla>_<columnas>`, `ck_<tabla>_<nombre>`. Los existentes (`idx_audit_fecha`) se quedan |
| Comentarios | tablas y columnas nuevas llevan `comment=` en español: se ven en pgAdmin y DBeaver |

## 3. Migraciones (Alembic)

- Carpeta `migraciones/`. Archivo `NNNN_descripcion.py` con `revision = "NNNN"` secuencial
  (`0001_esquema_inicial.py`, `0002_emisiones.py`). La descripción va en español.
- **Una migración por cambio**, con `upgrade` y `downgrade` probados en SQLite y Postgres.
- Portables: `op.create_table`, `op.add_column`, `op.create_index`. SQL propio de un dialecto
  solo dentro de una rama explícita por `op.get_bind().dialect.name`. En SQLite, los cambios a
  columnas existentes van con `op.batch_alter_table`.
- **Una migración publicada no se edita.** Si algo quedó mal, se corrige con otra.
- Sin datos reales (L14). Datos de referencia, solo si el motor no arranca sin ellos.
- Cada cambio de esquema que llegue de upstream (hoy son `ALTER` condicionales en los `init_*`)
  se traduce a una migración nueva. `core/datos.py` ignora ese DDL al vuelo, y
  `tests/unit/test_esquema.py` falla mientras la migración no exista.
- Después de cada migración: `python tools/generar_dbml.py` (actualiza [`schema.dbml`](schema.dbml); CI lo exige).
- En un despliegue, `alembic upgrade head` es un paso explícito antes de levantar el servicio.

## 4. API

| Tema | Regla |
|---|---|
| Rutas | bajo `/api/v1`, en español, con el estilo existente: recurso y acción (`/dte/emitir`, `/dte/{dte_id}/enviar`, `/dte/{dte_id}/estado`) |
| Autenticación | solo `Authorization: Bearer <token>`. Ninguna cabecera propia obligatoria |
| Compatibilidad | **no se quita ni se renombra** un campo ni una ruta. Lo nuevo entra como campo opcional o ruta nueva |
| Errores | el formato uniforme de `core/errors.py`: `{"codigo", "mensaje", "detalle"}`. `codigo` es estable y en `snake_case`, para que el producto ramifique; `mensaje` es para personas. Nunca secretos ni trazas en ninguno de los dos |
| Errores nuevos | subclases de `core/errors.py` (`ValidacionError`, `ConflictoError`, `RecursoNoEncontrado`…), no `HTTPException` sueltos |
| Códigos HTTP | 200 o 201 · 401 sin credencial · 403 sin permiso · 404 no existe · 409 conflicto (`id_externo` en curso, documento ya enviado, `IntegrityError`) · 422 datos inválidos · 502 el SII falló (`SIIError`) |
| Montos y fechas en JSON | como hoy: montos enteros en pesos y fechas `AAAA-MM-DD` |
| Listados | `limit` y `offset`, como los listados actuales |

## 5. Código

- Capas para lo nuevo: rutas (`api/routes/`, HTTP y validación) → servicios (`core/servicios/`,
  dueños de la transacción) → acceso a datos (`core/database.py` y repositorios) y adaptadores
  (`core/adaptadores/`: SII con token en caché y sin escribir en la base, caché memoria/Redis).
  El corazón SII se llama, no se modifica (L2).
- Rutas `def` si hacen trabajo bloqueante (base, firma, disco, SII): FastAPI las corre en su pool
  de hilos. `async def` solo si no bloquean o si envuelven lo bloqueante en `run_in_threadpool`.
  Lo que compartan los hilos lleva candado.
- Ruta nueva: decide si la usa un token `integracion`. Si sí, su prefijo va en
  `core/autorizacion.py` (si no, el token recibe 403). Si devuelve algo de un documento, llama a
  `exigir_documento_propio`. La protección va con `requerir_autenticacion` (o
  `requerir_administrador`), que recibe el `Request`.
- Configuración solo por `core.config.settings`; nada de `os.environ` en código nuevo.
- Hora solo por `core/reloj.py`.
- Logs en español, sin secretos, sin certificados y sin RUT reales.
- Pruebas: pytest en `tests/` (`tests/api/`, `tests/unit/`); archivos `test_<tema>.py` y
  funciones `test_<comportamiento>` en español (`test_reintento_devuelve_mismo_folio`). Solo
  RUT ficticios (L14).

## 6. Fork y upstream

- **Archivos nuevos antes que modificar existentes.** Si hay que tocar un archivo de upstream, el
  cambio es chico y queda concentrado. Así el merge es predecible.
- `nucleo/`, `nucleo-rs/`, `app/`, `app-rs/` y el MCP no se tocan. Si una ruta Python cambia de
  respuesta, la divergencia se anota en `tools/contrato_api.py`, no se replica en Go ni en Rust.
- Documentos del fork en `docs/deploy/`. Los documentos de upstream se corrigen solo si dicen algo
  falso (L11).
- Commits en español, con el estilo del repositorio: `Área: qué cambia`
  (`Datos: migración 0002 con la tabla emisiones`).
- **Traer cambios de upstream** es un paso fijo:
  1. `git fetch` del repositorio original y merge en una rama aparte.
  2. Traducir a migraciones los cambios de esquema que traiga.
  3. Correr `test_dorado` 15/15, la suite en SQLite y Postgres y `tools/auditar_publico.py`.
  4. Recién entonces, merge a `servidor-produccion`.
