# Plan del fork: Faro como API de facturación para integraciones

> **Estado (2026-11-09):** fases 0 a 5 hechas; lo demás, pendiente salvo lo marcado ✅. Rama de
> trabajo: `servidor-produccion`. Los hallazgos están **verificados en vivo** contra la imagen
> Docker del motor Python (`tools/sondeo_integridad.py`).
>
> **Decidido:** se mejora **en el lugar**, sin paquete ni proyecto nuevo. El motor ya funciona en
> producción y otra base de código exigiría volver a probar contra el SII real. El estándar de
> [`ARQUITECTURA.md`](ARQUITECTURA.md) se aplica a cada parte que se toca. Alcance de esta etapa:
> **base de datos y migraciones, transacciones y despliegue**. Go, Rust, las apps y el MCP quedan
> intactos para poder traer cambios del repositorio original. Reglas de nombres, migraciones y API:
> [`CONVENCIONES.md`](CONVENCIONES.md).

## Objetivo

Una instancia de Faro por empresa cliente. Tus sistemas (las **integraciones**: punto de venta, tienda, ERP) la llaman por la red interna para
emitir, enviar al SII y consultar. El resultado tiene que ser:

- **completo**: todo lo que el motor ofrece hoy sigue disponible. Se agrega lo que una integración
  necesita (emisor y certificado tomados de la instancia, idempotencia, envío y estado por
  documento) y una autenticación simple: **un administrador y un token por integración**;
- **seguro ante reintentos y concurrencia**: sin duplicados, sin folios perdidos en silencio y
  sin sobrescribir documentos;
- **con migraciones controladas** (Alembic) y **PostgreSQL** gestionable desde herramientas
  externas, sin perder SQLite para local y pruebas;
- **con un estándar de API reconocible**: capas, transacción por caso de uso, política async,
  caché donde corresponde ([`ARQUITECTURA.md`](ARQUITECTURA.md)).

**Fuera de alcance (roadmap):** motores Go y Rust, MCP, app de escritorio y migración de datos
SQLite → Postgres (las instancias del fork parten de cero).

## Verificación (2026-10-15, imagen `faro-dte:local`, SQLite)

| # | Prueba | Resultado | Gravedad |
|---|---|---|---|
| 1 | `POST /api/v1/auth/registro` sin token | **201**: cualquiera en la red crea un usuario, inicia sesión, crea API keys (201) y lista todos los documentos (200) | crítica · ✅ corregido (fase 2) |
| 2 | 20 emisiones simultáneas con folio automático | **OK**: folios 1..20 únicos y consecutivos (`BEGIN IMMEDIATE`) | — |
| 3 | Reintento de la misma petición | **Duplicado**: folios 21 y 22 para una sola venta. No hay idempotencia | crítica · ✅ corregido (fase 2) |
| 4 | `folio` explícito ya usado | **500**. `folio_siguiente` **retrocede** de 23 a 2, **se sobrescribe el PDF** original y las emisiones automáticas siguientes también fallan pisando archivos (`core/models.py:282`; los archivos se escriben antes del `INSERT`) | crítica · ✅ corregido (fase 2) |
| 5 | Factura con `GiroRecep` de 80 caracteres (el XSD admite 40) | **200**: firmada, guardada, folio gastado. Las facturas no se validan con XSD; las boletas sí | alta · ✅ corregido (fase 2) |
| 6 | Error tras consumir el folio | Por código: el folio se confirma en su propia transacción y la fila al final. Un error entre medio lo deja gastado sin registro | alta · ✅ corregido (fase 2) |
| 7 | `IntegrityError` | 500 y conexión HTTP cortada | media · ✅ corregido (fase 2) |
| 8 | `contexto-emision` | entrega certificado y **clave privada en PEM** a cualquier JWT | alta · ✅ corregido (fase 2) |
| 9 | Imagen construida desde Windows | `autocrlf` → corpus dorado 0/15; con LF, **15/15** | media · ✅ corregido (fase 0) |
| 10 | N+1 | **No hay** en la API: listados paginados, ~5 consultas por emisión | — |
| 11 | Dependencias sin fijar | `mcp` 2.x rompe `mcp_server.py` | media · ✅ corregido (fase 0) |
| 12 | Event loop | `/health` de 19 ms a **1.533 ms** con 8 emisiones en paralelo: 72 rutas `async def` con trabajo bloqueante | alta · ✅ corregido (fase 3) |
| 13 | Token del SII | se pide de nuevo en cada envío o consulta (un `ClienteSII` por petición) | media · ✅ corregido (fase 3) |
| 14 | Veredicto del SII | ningún flujo guarda `aceptado`/`rechazado` en `dtes`; queda en `enviado` | media · ✅ corregido (fase 5) |
| 15 | Notas 56/61 | `core/dte.py` no exige referencias; el SII sí | media · ✅ corregido (fase 2) |
| 16 | Envío de boletas | sin ruta en Python (el cliente REST existe y está verificado en producción) | alta · ✅ corregido (fase 5) |

Gráfico del recorrido con estos puntos marcados: artifact "Recorrido del comprobante".

## Decisiones

| Tema | Decisión |
|---|---|
| Forma | **Mejorar en el lugar** con el estándar de [`ARQUITECTURA.md`](ARQUITECTURA.md): servicios con su transacción, repositorios y migraciones dentro de `core/` y `api/`. `core/sobre.py` y la generación del XML no se tocan |
| Repositorio | rama `servidor-produccion`. `nucleo/`, `nucleo-rs/`, `app/`, `app-rs/` y el MCP **se quedan como están** para traer cambios del repositorio original |
| Certificado | **Ya se guarda**: el `.p12` y su contraseña van cifrados con Fernet (`DTE_MASTER_KEY`) en el keystore de la base (`core/keystore.py`, columnas `p12_cifrado` y `clave_cifrada`), nunca en claro ni en una ruta pública (L6). Se sube **una vez** por el administrador. La emisión lo toma del negocio de la instancia (`negocios.cert_id`): la integración no lo envía. Se descifra una vez por proceso y queda en memoria, y se invalida al reemplazarlo. `DTE_MASTER_KEY` vive en los secretos del despliegue, no en `storage/` ni en la base |
| Datos | SQLAlchemy 2 Core + **Alembic** desde el primer cambio de esquema; SQLite y Postgres con el mismo código y las mismas migraciones |
| Transacción | una por caso de uso; el servicio es su dueño |
| Idempotencia | **Sin cabeceras ni claves que generar**: un campo `id_externo` en el mismo JSON (no se llama `referencia` para no confundirlo con `dte.referencias`, las referencias a otros documentos del SII), con el identificador que la integración ya tiene (pedido, venta, orden). Único por token de integración. Es obligatorio para los tokens de integración y opcional en la ruta actual: la UI `/app`, los `.http` y `tools/contrato_api.py` siguen funcionando sin él. La verdad está en la base, en la misma transacción que el folio. Reglas en [Política de folios](#política-de-folios) |
| Autenticación | un **administrador** (JWT) y **tokens de integración** (uno por sistema), ligados a su cuenta, con rol `integracion`: emitir, enviar, consultar. Gestión (keystore, CAF, tokens) solo el administrador. Registro público cerrado |
| Superficie | **no se corta nada**: las rutas actuales siguen montadas; las nuevas se suman. El emisor y el certificado pasan a ser opcionales (los completa el negocio de la instancia). Las únicas restricciones son de seguridad (#1, #8); cambian respuestas que `tools/contrato_api.py` compara con Go y Rust, así que en el fork ese gate excluye esos pasos (Go y Rust no se tocan) |
| Caché | Redis opcional: token SII, resolución, límite de login y candado de líder. Nunca folios ni idempotencia |

## Fases

Cada fase cierra con pruebas en verde en **SQLite y Postgres** (desde la fase 1) y `test_dorado`
15/15. **Orden (cambiado el 2026-10-29):** 0 → 5 y la 6 al final. Sin VPS todavía y sin pruebas
reales contra el SII, se despliega una sola vez, ya con envío al SII, tokens por integración y la API
estable bajo carga.

### Fase 0 — Base de trabajo
- ✅ `http/` con REST Client (80 operaciones generadas por `tools/generar_http.py` + flujos a mano).
- ✅ `tools/sondeo_integridad.py` (reproduce #1, #3, #4, #5).
- ✅ Diagnóstico de arquitectura ([`ARQUITECTURA.md`](ARQUITECTURA.md)).
- ✅ **Los 13 `test_*.py` de la raíz están en `tests/scripts/`** (con `git mv`, la historia se
  conserva). `tests/scripts/_raiz.py` deja la raíz en `sys.path` y como directorio de trabajo, así
  que se corren igual que antes: `python tests/scripts/test_x.py`. Referencias corregidas en CI,
  `tools/prueba_final.sh`, `CLAUDE.md`, `AGENTS.md`, docs y agentes. En la imagen pasan
  `test_dorado` (15/15), `test_simulacion` (29), `test_mvp`, `test_mipyme` y ahora también
  `test_robustez` (gracias a `mcp` 1.x). Los que usan el Llavero de macOS (`test_sobre`,
  `test_boleta`, `test_libro`) y el binario Go (`test_fase2`) siguen igual que antes. Los
  comentarios de `nucleo/` y `nucleo-rs/` que nombran `test_dorado.py` no se tocan (Go y Rust
  intactos). En Windows, `test_simulacion` marca 1 de 29 por el separador de rutas (`\`): es previo
  al cambio y no ocurre en Linux.
- ✅ pytest (`pytest.ini`, `requirements-dev.txt`, `tests/conftest.py`): instancia limpia con base
  y `storage/` temporales, secretos de prueba, programadores apagados, sin red (la ficha del SII
  se simula) y CAF y certificado ficticios. `tests/unit/test_folios.py` (50 hilos, gate de la fase
  1) y `tests/api/`. Los hallazgos #1, #3, #4/#7, #5, #8 y #15 son `xfail(strict=True)`: fallan
  hoy por la razón esperada (verificado con `--runxfail`), y al corregirlos la suite se pone roja
  hasta quitar la marca. Resultado: 4 verdes y 7 `xfail`, en Windows y en Linux.
- ✅ `.gitattributes`: LF en todo el árbol de trabajo, también con `core.autocrlf=true`; los XSD
  del SII quedan byte a byte como están. Una imagen construida desde Windows da 15/15 (#9).
- ✅ `constraints.txt`: versiones fijadas resueltas en `python:3.12-slim`, aplicadas con
  `pip install -r requirements.txt -c constraints.txt` (el `Dockerfile` ya lo hace;
  `requirements.txt` queda como upstream). `mcp` en 1.30 (#11).
- ✅ CI propio (`.github/workflows/servidor.yml`): suite pytest, imagen, corpus dorado dentro de
  la imagen y `/health`.

### Fase 1 — Datos y migraciones ✅ (2026-10-29)
Detalle y verificación en [`POSTGRESQL.md`](POSTGRESQL.md); tablas en [`schema.dbml`](schema.dbml).
- ✅ **Gate de concurrencia primero**: `tests/unit/test_folios.py` (50 hilos sobre el mismo CAF,
  folios 1..50 sin huecos) en verde antes y después del cambio, en SQLite y en PostgreSQL.
  `datos.transaccion(inmediata=True)`: `BEGIN IMMEDIATE` en SQLite y `SELECT … FOR UPDATE` en
  PostgreSQL. Para no cambiar el comportamiento, SQLite conserva el manejo de transacciones de
  `sqlite3` y no usa pool.
- ✅ `core/datos.py`: `Engine` de SQLAlchemy y una conexión con la forma de `sqlite3`
  (`execute`, `fetchone`, `rowcount`, `lastrowid`, `executemany`, `executescript`, filas como
  `sqlite3.Row`), así que las consultas existentes no cambiaron. Los 8 `lastrowid` funcionan en
  PostgreSQL con `RETURNING id` automático, sin tocar sus archivos. `core/models.py` ya no abre
  su propia conexión `sqlite3`.
- ✅ `DATABASE_PATH` solo existe con SQLite (las dos rutas de la app de escritorio devuelven
  `null` con PostgreSQL); el log de arranque muestra la URL sin contraseña. Si un script
  reasigna `DATABASE_PATH` (varios scripts de upstream lo hacen), la conexión lo sigue.
- ✅ Alembic: `alembic.ini`, `migraciones/` y `0001_esquema_inicial` (13 tablas). En SQLite es el
  DDL heredado copiado literal, así que el esquema queda idéntico al de siempre y al de Go; en
  PostgreSQL, las mismas tablas con `BIGINT`, `DOUBLE PRECISION` y `BYTEA`. `init_db()` corre
  `alembic upgrade head`; `setup.py`, los scripts y `uvicorn` arrancan solos. Una base heredada
  (sin `alembic_version`) se completa con el DDL de siempre y se marca `0001`, sin perder datos.
  `downgrade` probado.
- ✅ El DDL heredado que los módulos corren al vuelo se ignora (el esquema es de Alembic), y
  `tests/unit/test_esquema.py` exige que ese DDL y las migraciones den el **mismo**
  `sqlite_master`: un cambio de esquema traído de upstream sin su migración rompe la suite.
- ✅ `tools/esquema_dorado.py` ignora `alembic_version`.
- ✅ PostgreSQL con psycopg 3. `tests/unit/test_esquema.py` compara la estructura de Postgres con
  la de SQLite (tablas, columnas, nulos, claves, únicos, índices). `datetime('now')` de
  `core/sii_mipyme.py` pasó a parámetro. `LIKE` no requirió cambios: solo compara fechas.
- ✅ `docker-compose.postgres.example.yml` (Postgres 17 y pgAdmin opcional) con
  `alembic upgrade head` como paso explícito; 10 emisiones simultáneas por HTTP dieron folios 1..10.
- ✅ `tools/generar_dbml.py` escribe [`schema.dbml`](schema.dbml) desde una base migrada; CI exige
  que esté al día. CI corre la suite en SQLite y en PostgreSQL.
- ✅ Sin regresiones: los scripts históricos pasan en la imagen y `tools/sondeo_integridad.py`
  da los mismos 8 hallazgos que antes del cambio.

### Fase 2 — Emisión transaccional e idempotencia ✅ (2026-10-29)
Regla decidida con el usuario: **timbrado = emitido** ([Política de folios](#política-de-folios)).
`POST /api/v1/dte/emitir` usa el servicio `core/servicios/emision.py`; el MCP, la simulación y
los scripts siguen con `core/orchestrator.py::emitir_documento`, sin cambios.
- ✅ **Prevalidación sin folio** (#5, #15): RUT de emisor y receptor, referencias obligatorias en
  56/61 y un **ensayo** completo (TED, firma, sobre y XSD con un folio de prueba, sin guardar
  nada). El XSD juzga largos y obligatorios, así que no hay una tabla de límites a mano que
  mantener. Falla → 422 sin gastar folio. `core/dte.py` no se tocó.
- ✅ **Reserva en una transacción** (#3, #6): `id_externo`, folio y fila `reservado` de `dtes`, con
  el candado del CAF (`models.bloquear_caf_activo`, el mismo de `consumir_siguiente_folio`, L7).
  Antes de avanzar `folio_siguiente` se reusa el folio de una fila sin timbre (`error`, o
  `reservado` de más de `EMISION_PLAZO_RESERVA_MIN`, 10 minutos).
- ✅ **Generación** con el folio real: TED, firma, sobre, **XSD también en facturas**, PDF. Archivos
  en `storage/emisiones/<dte_id>/v<intento>/`, abiertos en modo exclusivo: nunca se sobrescriben
  (#4). Cierre a `generado` solo si la fila sigue en ese intento. Si algo falla, `error` con
  `motivo_error`, y la respuesta trae `dte_id`, `folio` y `estado: error`.
- ✅ Folio explícito solo para el administrador (403 para el resto), dentro del CAF y nunca hacia
  atrás: 409 si ya se usó, y `folio_siguiente` no retrocede (#4).
- ✅ `IntegrityError` → 409 `conflicto`, sin SQL en la respuesta (#7).
- ✅ **Certificado del negocio** (keystore, `negocios.cert_id`): se descifra una vez por proceso y
  otra vez si se reemplaza. `certificado_p12_b64` en el cuerpo sigue por compatibilidad; sin
  ninguno de los dos, `CERTIFICADO_PATH` como siempre. Una API key usa la cuenta dueña.
- ✅ **Fugas** (#1, #8): `/auth/registro` y `DELETE /auth/usuarios/{id}` solo para el administrador;
  `contexto-emision` y `mipyme/contexto-siiengine` (entregan la clave privada y las claves del SII)
  solo con la clave de la app (`APP_SHELL_KEY`).
- ✅ Migración `0002`: tabla `emisiones` (`origen` + `id_externo` únicos, `huella`, `dte_id`) y
  columnas `dtes.intento` y `dtes.motivo_error`. `tools/esquema_dorado.py` compara con Go hasta
  `0001`; `test_esquema.py` exige el DDL heredado igual a `0001`.
- ✅ El RVD y la consulta pública ignoran las filas sin timbre (`reservado`, `error`).
- ✅ `core/orchestrator.py` y `core/orchestrator_boleta.py` exponen sus pasos (`_caf_activo`,
  `_timbrar`, `generar_documento`, `generar_pdf`) para que el servicio no duplique la firma (L2);
  sus bytes no cambiaron (`test_dorado` 15/15).
- ✅ Verificación: suite pytest 29 + 1 omitida en SQLite y 30 en PostgreSQL 17 (los 7 `xfail` de la
  fase 0 pasaron a pruebas normales, más `tests/api/test_politica_folios.py`); `test_dorado`
  15/15 en local y en la imagen; `tools/sondeo_integridad.py` contra la imagen nueva: **0
  hallazgos** (antes 8); la base PostgreSQL local con 11 documentos se actualizó de `0001` a
  `0002` sin perder datos.

### Fase 3 — Política async, caché y adaptadores ✅ (2026-10-29)
- ✅ **Rutas en el pool de hilos (#12):** las 67 rutas de `api/routes/` que no esperaban nada (o
  solo `leer_upload` o una dependencia de auth) pasaron de `async def` a `def`; FastAPI las corre
  en su pool. Las 9 de `simulacion` y `mipyme` ya usaban `run_in_threadpool`. Las dependencias de
  `core/auth.py` también son `def` (consultan la base). Las rutas de `main.py` (páginas y
  `/health`) siguen `async`: no bloquean. Medido contra la imagen con 8 emisiones en paralelo:
  `/health` llega a **206 ms** (imagen de la fase 2: 2.006 ms). Prueba:
  `tests/api/test_concurrencia.py` y la sección 6 de `tools/sondeo_integridad.py`.
- ✅ **Seguro entre hilos:** las rutas ahora corren en paralelo. Lo único compartido que no lo era
  es el `XMLSchema` de lxml (guarda su `error_log` en el objeto): `core/schema_validator.py`
  valida de a uno por esquema. Folio y emisión ya tenían su candado en la base (fases 1 y 2).
- ✅ **Adaptadores (#13):** `core/adaptadores/sii.py` (`ClienteFacturas`, `ClienteBoletas`) son
  los clientes de siempre con el token compartido por certificado, ambiente y servicio (50
  minutos) y **sin escribir en la base** (`models.persistir_envios`). `/dte/enviar` y
  `/estado/*` los usan; `/dte/enviar` marca los documentos él mismo, como antes.
  `core/sii.py` y `core/sii_boleta.py` no se tocaron.
- ✅ **Caché** `core/adaptadores/cache.py`: memoria del proceso, o **Redis** con `REDIS_URL`
  (valores cifrados con Fernet: un token del SII es una credencial). `redis` en
  `requirements.txt`; CI corre la suite con Redis.
- ✅ Verificación: suite 35 + 6 omitidas en SQLite sin Redis, 41 en PostgreSQL 17 con Redis;
  `test_dorado` 15/15; `sondeo_integridad` contra la imagen: 0 hallazgos.
- Pendiente para cuando haya réplicas (roadmap): candado de líder para el RVD y la reposición
  de folios. (El límite de intentos de login llegó en la fase 4.)

### Fase 4 — Roles, tokens, auditoría y rutas sin `/db` ✅ (2026-10-30)
Una empresa por despliegue: no hay entidad central ni tabla de integraciones.
- ✅ **Dos roles** (`core/autorizacion.py`). `administrador`: los usuarios con login (JWT) y la
  clave de la app; hacen todo. `integracion`: los tokens `dte_…` de cada sistema que llama a la
  API (punto de venta, tienda, ERP). El token emite, envía, consulta y gestiona certificados,
  negocios, claves del SII, CAF y folios, sobre la **cuenta dueña** (el primer usuario, como la
  clave de la app). Recibe `403` en usuarios, tokens (salvo renovar el suyo), auditoría, listado
  general de documentos, `/db/*`, `simulacion`, `mipyme` y `onboarding`. La lista de lo
  permitido es una sola (`PERMITIDO_INTEGRACION`, por prefijo de la plantilla de la ruta): lo
  que no está queda negado. La revisa `requerir_autenticacion`, que ahora recibe el `Request`.
- ✅ **Cada token ve solo lo suyo.** `dtes.origen` guarda quién emitió (`usuario:<id>` o
  `apikey:<id>`, el mismo formato que el `origen` de `emisiones`). `/dte/documentos/{id}`, su
  PDF, su XML y `/dte/documentos/buscar` responden `404` al token que no lo emitió (no revela
  que existe). Lo emitido por otras rutas o antes de esta fase tiene `origen` NULL: solo el
  administrador lo ve.
- ✅ **Tokens sin vencimiento, renovables.** Renovar (`POST /apikeys/{id}/renovar`, el
  administrador; `POST /apikeys/propia/renovar`, el token mismo) cambia el secreto y conserva el
  `id`: su `id_externo` y su rastro en la auditoría siguen. La clave anterior muere al instante,
  igual que al revocar; no hay plazo de gracia. Crear, listar, renovar y revocar exigen un
  usuario con login (ni un token ni la clave de la app, como en Go). `GET /auth/me` con un token
  responde su id, nombre y rol. `ultimo_uso` se escribe como mucho una vez por minuto.
- ✅ **Rutas sin `/db`.** Pasan a su dominio, con la misma implementación:

  | Antes | Ahora |
  |---|---|
  | `GET /db/dtes` | `GET /dte/documentos` (ya existía; suma `fecha_desde`, `fecha_hasta` y `offset`) |
  | `GET /db/dtes/{id}` | `GET /dte/documentos/{id}` |
  | `GET /db/dtes/buscar/{rut}/{tipo}/{folio}` | `GET /dte/documentos/buscar?rut_emisor=&tipo_dte=&folio=` |
  | `GET /db/cafs` · `POST /db/cafs` | `GET /caf` · `POST /caf` |
  | `GET /db/cafs/activo/{rut}/{tipo}` | `GET /caf/activo/{rut}/{tipo}` |
  | `GET /db/folios/siguiente/{rut}/{tipo}` | `GET /caf/folios/siguiente/{rut}/{tipo}` |
  | `GET /db/logs` | `GET /auditoria` (suma el filtro `actor`) |

  `/db/*` queda como alias **obsoleto, solo para el administrador y fuera de `/docs`**: Go y Rust
  la siguen sirviendo y `tools/contrato_api.py` la compara. Las rutas nuevas son solo de Python.
- ✅ **Auditoría de quién hace qué** (`api/auditoria.py`). `audit_log` suma `actor`, `metodo`,
  `ruta` (la plantilla) y `estado_http`. Se registra toda petición a `/api/` con `POST`, `PUT`,
  `PATCH` o `DELETE`, y todo login (`login`, `login_fallido`, `login_bloqueado`, con el usuario
  intentado). Nunca el cuerpo. La emisión anota además su `actor`.
- ✅ **Seguridad común** (`core/limites.py`, contadores en la caché de la fase 3, memoria o
  Redis, con las claves en hash). Login: 5 fallos por usuario+IP bloquean 15 minutos
  (`LOGIN_MAX_FALLOS`, `LOGIN_BLOQUEO_MIN`); `429` con `Retry-After`, incluso con la contraseña
  correcta, y los demás usuarios siguen entrando. Mismo mensaje y mismo tiempo exista o no el
  usuario (hash señuelo). Bearer inválidos: pasados 20 por IP (`BEARER_MAX_FALLOS`), los inválidos
  responden `429`; los válidos pasan siempre (un token de 256 bits no se adivina, y bloquear la IP
  cortaría a los demás sistemas que salen por ella). Contraseña nueva de 12 caracteres como
  mínimo (`CLAVE_LARGO_MINIMO`). `POST /auth/cambiar-clave` invalida los JWT anteriores: el JWT
  lleva la fecha del último cambio (`cc`) y deja de coincidir. Las respuestas de error conservan
  `WWW-Authenticate` y `Retry-After` (antes el manejador las perdía).
- ✅ **Migración `0003`:** `api_keys.rol`, `creado_por` y `renovado`; `audit_log.actor`, `metodo`,
  `ruta`, `estado_http` (con índice por `actor`); `usuarios.clave_cambiada_en`; `dtes.origen`. Los
  tokens existentes quedan `integracion`. Probada de subida y de bajada en SQLite y PostgreSQL, y
  sobre la base local con datos (0002 → 0003).
- ✅ Verificación: suite 67 + 6 omitidas en SQLite sin Redis, 73 en PostgreSQL 17 con Redis
  (`tests/api/test_autorizacion.py`: matriz de permisos, documentos propios, renovar y revocar,
  login, contraseña, Bearer y auditoría); `test_dorado` 15/15 local y en la imagen;
  `sondeo_integridad` contra la imagen: 0 hallazgos (sección 7 nueva: roles y límites).
- Fuera de esta fase: la pantalla `/apikeys` (UI local) no tiene botón de renovar; la ruta sí.

### Fase 5 — API para integraciones ✅ (2026-11-09)
"Integración" es el sistema que llama a la API (punto de venta, tienda, ERP); "producto" queda
para lo que se vende en la factura. El motor ya sabía enviar (facturas aceptadas en certificación,
boletas en producción); faltaba unirlo a la API del servidor por documento.
- ✅ **Flujo decidido: Faro despacha** (`core/servicios/envio.py`). Emitir responde al instante
  con folio y PDF (el veredicto nunca cabe en esa respuesta: el SII decide después) y, con
  `enviar` (por defecto), el despacho en segundo plano envía el **sobre guardado al emitir**, tal
  cual (L1), y consulta el veredicto: facturas por SOAP, boletas por REST (#16), cada una con su
  token (L9). La integración consulta `GET /dte/{dte_id}/estado`. Con `"enviar": false` maneja el
  envío ella (`POST /dte/{dte_id}/enviar`); las mismas rutas.
- ✅ **Veredicto guardado** (#14) desde `ACEPTADOS`/`RECHAZADOS`/`REPAROS` del documento, nunca
  `EPR` (L3): `enviado` → `aceptado` | `aceptado_con_reparos` | `rechazado`. El código del rechazo
  de una boleta queda en `motivo_sii`; el de una factura llega solo por correo y `motivo_sii` lo
  avisa. `?consultar_sii=true` pregunta en el momento (como mucho cada 30 s).
- ✅ **Reintentos y alertas.** Envío fallido: reintento con espera creciente (1, 2, 4… hasta 60
  min); a los `ENVIO_MAX_INTENTOS` (8), `envio_fallido`. Consultas cada vez más espaciadas (20 s →
  1 h); a las `VEREDICTO_ALERTA_HORAS` (6) sin veredicto, `sin_veredicto` (se sigue preguntando).
  `GET /dte/alertas` (administrador): `sin_veredicto`, `envio_fallido` y `folios_por_anular`
  (rechazados: el folio quedó quemado). Cada hito queda en la auditoría.
- ✅ **Varios procesos sin choque:** cada documento se toma con `dtes.tomado_hasta` (UPDATE
  condicional, igual en SQLite y PostgreSQL); un envío a mano sobre uno tomado responde 409
  `en_curso`. El despacho corre en el lifespan (asyncio + hilo), como el RVD.
- ✅ **`ENVIO_SII=desactivado` por defecto:** se emite igual y nada sale al SII (`envio:
  "desactivado"`, enviar a mano → 409 `envio_desactivado`). Así se desarrolla con el CAF y el
  certificado ficticios sin mandarlos al SII. En producción desactivado, aviso al arrancar.
- ✅ **Emisor opcional** en `emitir` y `previsualizar`: sin `emisor`, el del negocio (con varios,
  `rut_emisor`); si le faltan datos, 422 con la lista. La huella del reintento sale del JSON tal
  como llegó: cambiar los datos del negocio no convierte un reintento en corrección.
  `previsualizar` acepta además sin `cert_id` (certificado del negocio).
- ✅ **BORRADOR** sobre el PDF de `previsualizar` (`core/pdf_borrador.py`, pypdf), sin tocar
  `core/pdf_gen.py`.
- ✅ **Migración `0004`:** columnas de despacho en `dtes` (`seguimiento`, `intentos_envio`,
  `proxima_accion`, `tomado_hasta`, `enviado_en`, `consultado_en`, `veredicto_en`, `motivo_sii`) e
  índice `idx_dtes_despacho`.
- ✅ `http/api/` regenerado (85 operaciones), `http/flujos/02-cliente.http` con los pasos 13 a 18,
  [`INTEGRACION.md`](INTEGRACION.md) reescrito como guía de integración. "Producto" → "integración"
  en los documentos del fork.
- ✅ Verificación: suite 83 + 7 omitidas en SQLite sin Redis (`tests/api/test_envio.py`, 15 pruebas
  con el SII falso); PostgreSQL 17 con Redis; `test_dorado` 15/15; `sondeo_integridad` con la
  sección 8 (despacho desactivado en la imagen).
- Pendiente fuera del código: **probar el despacho contra el SII de certificación** con CAF y
  certificado reales (envío, veredicto, rechazo de boleta con su código). Por verificar ahí: si la
  factura debe estar aceptada antes de entregarla al receptor.

### Fase 6 — Despliegue (al final)
`docker-compose.example.yml` con Postgres (y Redis opcional), `alembic upgrade head` como paso
explícito, respaldo con `pg_dump` + volumen `storage/` + `.env`.

## Política de folios

Un folio es el número legal del documento. **Regla: timbrado = emitido** (decidido 2026-10-29).
Timbrar es local: el motor firma el TED con la clave del CAF que el SII entregó por adelantado,
sin hablar con el SII. Desde ese momento el PDF puede estar en manos del cliente (una boleta
impresa), así que **un folio timbrado es de esa venta para siempre**: no se reasigna ni se
regenera con otros datos. Enviar al SII y recibir su veredicto viene después (fase 5) y no
cambia esto. Solo vuelve a usarse un folio que nunca llegó a timbrarse.

| Dónde falla | Qué pasa con el folio | Qué hace el motor |
|---|---|---|
| **Antes de reservar** (datos inválidos) | no se toca | 422 con el detalle. Es donde deben caer casi todos los errores: la prevalidación y el ensayo existen para eso |
| **Después de reservar, antes de quedar timbrado** (disco, caída del proceso) | nadie tiene un papel con ese número | la fila queda `error` (o `reservado` huérfana). El mismo `id_externo` lo retoma con el mismo folio, con el JSON igual o corregido; si no vuelve, la venta siguiente del mismo tipo toma ese folio. No hay hueco ni nada que anular |
| **Ya timbrado** (`generado`), esté o no enviado | puede estar entregado | el folio y sus datos quedan fijos. Un JSON distinto con el mismo `id_externo` responde 409 `ya_emitido` |
| **El SII lo rechaza** (`RCH`, `RFR`, `RCT`) | el SII lo da por usado | se guarda `rechazado` con el motivo (#14). Según proveedores chilenos, reenviar ese folio da `DTE-3-101` y el portal no deja anularlo: se emite un documento nuevo, con folio nuevo y otro `id_externo`. **A verificar en certificación** |
| **El SII lo aceptó** | es un documento legal | se corrige con nota de crédito o débito (61/56), que gasta un folio de ese tipo. Es el proceso legal, no un error del motor |

### El `id_externo`

La integración no genera ninguna clave ni agrega cabeceras. Manda su propio identificador de la
venta en el mismo JSON, con su token Bearer:

```http
POST /api/v1/dte/emitir
Authorization: Bearer <token de integración>
Content-Type: application/json

{ "id_externo": "pedido-1042", "dte": { "tipo_dte": 33, "receptor": { … }, "items": [ … ] } }
```

Respuesta: `dte_id`, `folio`, `estado` y los enlaces al XML y al PDF. Con esa sola regla
(**"una venta, un `id_externo`"**) el motor resuelve reintento y corrección:

| Llega el mismo `id_externo`… | Estado del documento | Respuesta |
|---|---|---|
| con el mismo JSON | timbrado (`generado` o posterior) | 200 con el documento existente: mismo `dte_id`, folio y sobre, `repetido: true`. Es el reintento tras un timeout (#3) |
| con el mismo JSON o uno **corregido** | `error`, o `reservado` huérfana | se genera de nuevo con el mismo folio, en un intento nuevo (`v2`, `v3`…); los archivos anteriores no se tocan |
| con el JSON cambiado | timbrado | 409 `ya_emitido` con `dte_id`, folio y estado: se corrige con nota de crédito o débito (o, si el SII lo rechazó, con un documento nuevo) |
| mientras otra petición la procesa | `reservado` reciente | 409 `en_curso`, sin esperar |

- La huella es un SHA-256 del JSON tal como llegó (sin `folio` ni valores por defecto).
- El `id_externo` es propio de cada credencial (`origen`: el usuario o la API key).
- Para revisar los datos **antes** de timbrar está `POST /api/v1/dte/previsualizar`, que no gasta
  folio. Hoy timbra con el primer folio del CAF; la fase 5 le pone la marca "BORRADOR".
- Sin `id_externo` (la UI, los `.http`, `tools/contrato_api.py`), cada petición es un documento
  nuevo, como siempre.

**Folios sin timbre.** El folio de una fila en `error` (o `reservado` de más de 10 minutos) lo toma
la siguiente emisión del mismo tipo antes de avanzar `folio_siguiente`, con el candado del CAF. Si
el `id_externo` dueño vuelve después, toma un folio nuevo. Así no quedan huecos que anular. Lo que
aun así quede sin usar (CAF vencido, por ejemplo) irá a un reporte "folios por anular" (fase 5).

## Riesgos

- **Firma intocable (L2, L15):** ninguna fase toca `core/sobre.py` ni la generación del XML
  (`core/dte.py`). Las validaciones nuevas (referencias 56/61, largos del XSD) van en el servicio.
  `test_dorado` es gate de cada fase.
- **Folios reales (L1, L7, L10):** las fases 1 y 2 cambian cómo se consume un folio. Se prueban
  con el CAF ficticio y en certificación antes de producción.
- **Distancia con upstream:** cada cambio en `core/database.py`, `core/models.py` y los
  orquestadores puede chocar al traer cambios del repositorio original. Los cambios se
  concentran en esos archivos y se mantienen chicos. La firma (`core/sobre.py`, `core/dte.py`)
  no se toca.
