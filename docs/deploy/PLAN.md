# Plan del fork: Faro como API de facturación para productos

> **Estado (2026-10-15):** plan aprobado; nada implementado salvo lo marcado ✅. Rama de
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

Una instancia de Faro por empresa cliente. Tus productos la llaman por la red interna para
emitir, enviar al SII y consultar. El resultado tiene que ser:

- **completo**: todo lo que el motor ofrece hoy sigue disponible. Se agrega lo que un producto
  necesita (emisor y certificado tomados de la instancia, idempotencia, envío y estado por
  documento) y una autenticación simple: **un administrador y tokens por producto**;
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
| 1 | `POST /api/v1/auth/registro` sin token | **201**: cualquiera en la red crea un usuario, inicia sesión, crea API keys (201) y lista todos los documentos (200) | crítica |
| 2 | 20 emisiones simultáneas con folio automático | **OK**: folios 1..20 únicos y consecutivos (`BEGIN IMMEDIATE`) | — |
| 3 | Reintento de la misma petición | **Duplicado**: folios 21 y 22 para una sola venta. No hay idempotencia | crítica |
| 4 | `folio` explícito ya usado | **500**. `folio_siguiente` **retrocede** de 23 a 2, **se sobrescribe el PDF** original y las emisiones automáticas siguientes también fallan pisando archivos (`core/models.py:282`; los archivos se escriben antes del `INSERT`) | crítica |
| 5 | Factura con `GiroRecep` de 80 caracteres (el XSD admite 40) | **200**: firmada, guardada, folio gastado. Las facturas no se validan con XSD; las boletas sí | alta |
| 6 | Error tras consumir el folio | Por código: el folio se confirma en su propia transacción y la fila al final. Un error entre medio lo deja gastado sin registro | alta |
| 7 | `IntegrityError` | 500 y conexión HTTP cortada | media |
| 8 | `contexto-emision` | entrega certificado y **clave privada en PEM** a cualquier JWT | alta |
| 9 | Imagen construida desde Windows | `autocrlf` → corpus dorado 0/15; con LF, **15/15** | media · ✅ corregido (fase 0) |
| 10 | N+1 | **No hay** en la API: listados paginados, ~5 consultas por emisión | — |
| 11 | Dependencias sin fijar | `mcp` 2.x rompe `mcp_server.py` | media · ✅ corregido (fase 0) |
| 12 | Event loop | `/health` de 19 ms a **1.533 ms** con 8 emisiones en paralelo: 72 rutas `async def` con trabajo bloqueante | alta |
| 13 | Token del SII | se pide de nuevo en cada envío o consulta (un `ClienteSII` por petición) | media |
| 14 | Veredicto del SII | ningún flujo guarda `aceptado`/`rechazado` en `dtes`; queda en `enviado` | media |
| 15 | Notas 56/61 | `core/dte.py` no exige referencias; el SII sí | media |
| 16 | Envío de boletas | sin ruta en Python (el cliente REST existe y está verificado en producción) | alta |

Gráfico del recorrido con estos puntos marcados: artifact "Recorrido del comprobante".

## Decisiones

| Tema | Decisión |
|---|---|
| Forma | **Mejorar en el lugar** con el estándar de [`ARQUITECTURA.md`](ARQUITECTURA.md): servicios con su transacción, repositorios y migraciones dentro de `core/` y `api/`. `core/sobre.py` y la generación del XML no se tocan |
| Repositorio | rama `servidor-produccion`. `nucleo/`, `nucleo-rs/`, `app/`, `app-rs/` y el MCP **se quedan como están** para traer cambios del repositorio original |
| Certificado | **Ya se guarda**: el `.p12` y su contraseña van cifrados con Fernet (`DTE_MASTER_KEY`) en el keystore de la base (`core/keystore.py`, columnas `p12_cifrado` y `clave_cifrada`), nunca en claro ni en una ruta pública (L6). Se sube **una vez** por el administrador. La emisión lo toma del negocio de la instancia (`negocios.cert_id`): el producto no lo envía. Se descifra una vez por proceso y queda en memoria, y se invalida al reemplazarlo. `DTE_MASTER_KEY` vive en los secretos del despliegue, no en `storage/` ni en la base |
| Datos | SQLAlchemy 2 Core + **Alembic** desde el primer cambio de esquema; SQLite y Postgres con el mismo código y las mismas migraciones |
| Transacción | una por caso de uso; el servicio es su dueño |
| Idempotencia | **Sin cabeceras ni claves que generar**: un campo `id_externo` en el mismo JSON (no se llama `referencia` para no confundirlo con `dte.referencias`, las referencias a otros documentos del SII), con el identificador que el producto ya tiene (pedido, venta, orden). Único por token de integración. Es obligatorio para los tokens de integración y opcional en la ruta actual: la UI `/app`, los `.http` y `tools/contrato_api.py` siguen funcionando sin él. La verdad está en la base, en la misma transacción que el folio. Reglas en [Política de folios](#política-de-folios) |
| Autenticación | un **administrador** (JWT) y **tokens de integración** por producto, ligados a su cuenta, con rol `integracion`: emitir, enviar, consultar. Gestión (keystore, CAF, tokens) solo el administrador. Registro público cerrado |
| Superficie | **no se corta nada**: las rutas actuales siguen montadas; las nuevas se suman. El emisor y el certificado pasan a ser opcionales (los completa el negocio de la instancia). Las únicas restricciones son de seguridad (#1, #8); cambian respuestas que `tools/contrato_api.py` compara con Go y Rust, así que en el fork ese gate excluye esos pasos (Go y Rust no se tocan) |
| Caché | Redis opcional: token SII, resolución, límite de login y candado de líder. Nunca folios ni idempotencia |

## Fases

Cada fase cierra con pruebas en verde en **SQLite y Postgres** (desde la fase 1) y `test_dorado`
15/15. **Esta etapa: fases 0, 1, 2 y 6.** Las fases 3, 4 y 5 son la etapa siguiente, salvo lo
que la fase 2 adelanta.

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

### Fase 1 — Datos y migraciones (prioridad)
Ver [`POSTGRESQL.md`](POSTGRESQL.md).
- **Primero, la prueba de concurrencia** (50 hilos sobre el mismo CAF, paso 1.5 de
  [`POSTGRESQL.md`](POSTGRESQL.md)) en verde sobre el código actual. Es el gate de todo lo que sigue:
  bajo SQLAlchemy, el driver de SQLite no emite `BEGIN` por su cuenta, y sin un evento que emita
  `BEGIN IMMEDIATE` el folio deja de ser atómico (L7) **sin dar ningún error**.
- `Engine` de SQLAlchemy detrás de `core/database.py`, sin cambio de comportamiento. Unos 50
  lugares usan `get_db()`/`get_connection()` como una `sqlite3.Connection` (`execute`, `cursor`,
  `rowcount`, `fetchone`, `executemany`): la fachada entrega un objeto con esa misma forma. Los 8
  `lastrowid` pasan a `insertar()` con `RETURNING`. `core/models.py:311` abre su propia conexión
  con `isolation_level=None`; pasa por la fachada.
- `DATABASE_PATH` (`core/database.py:18`) se calcula reemplazando texto en la URL y queda
  inválido con Postgres; lo usan `keystore` y `mipyme`. Se reemplaza por una función que solo
  aplica a SQLite y por la URL enmascarada.
- Alembic `0001_esquema_inicial` (13 tablas) para ambos dialectos; los `init_*` dejan de crear
  tablas e `init_db()` ejecuta `alembic upgrade head`, así que `setup.py`, los scripts y
  `uvicorn` siguen arrancando solos. **Base existente** (la local de hoy o una de upstream): si ya
  tiene las tablas y no tiene `alembic_version`, se marca con `stamp 0001` en vez de crearlas.
  `downgrade` probado.
- `tools/esquema_dorado.py` llama a `init_db()` y compara `sqlite_master` con Go: sigue sirviendo
  si ignora `alembic_version`.
- **Cambios de esquema de upstream:** hoy llegan como `ALTER` condicionales dentro de los `init_*`.
  Al traerlos, cada uno se traduce a una migración nueva; si no, la columna falta en silencio.
  Va como paso fijo de la guía para traer cambios de upstream.
- Postgres (psycopg 3), folio con `SELECT … FOR UPDATE`, compose local con Postgres y pgAdmin
  opcional. `LIKE` distingue mayúsculas en Postgres: la búsqueda de `valores` usa `ILIKE` o
  `lower()` para no cambiar el resultado.
- Prueba de **actualización**: base en la versión N con datos → `upgrade` → la suite pasa.

### Fase 2 — Emisión transaccional e idempotencia (lo más importante)
- **Prevalidación antes de tocar un folio:** largos y obligatorios del XSD (por ejemplo,
  `GiroRecep` ≤ 40), RUT, totales y referencias en 56/61 (#15). Se hace en el servicio, no en
  `core/dte.py`, que no se toca. Lo que falle aquí responde 422 y no gasta folio (#5).
- Servicio `EmitirDocumento`: reserva el `id_externo`, el folio y la fila `reservado` en **una**
  transacción. Después firma, validación XSD completa (facturas y boletas; el dry-run de
  certificación ya valida el EnvioDTE de facturas), archivos por `dte_id` sin sobrescribir y
  estado `generado`. Si falla, la fila queda en `error` con el motivo, y el folio sigue siendo de
  esa fila (#3, #6). Reglas de reintento y corrección en [Política de folios](#política-de-folios).
- Folio explícito solo para el administrador, validado contra `folio_siguiente` (#4).
- `IntegrityError` → 409 (#7).
- El servicio toma el **certificado del negocio** (keystore). `certificado_p12_b64` en el cuerpo
  queda solo por compatibilidad.
- Se adelantan dos fugas críticas, de pocas líneas: `/auth/registro` exige administrador (#1) y
  `contexto-emision` exige la clave de la app (#8).
- Migración Alembic `0002`: tabla `emisiones` y estados nuevos de `dtes`.

### Fase 3 — Política async, caché y adaptadores (etapa siguiente)
- Rutas bloqueantes como `def` o con `run_in_threadpool` (#12). Prueba: `/health` estable bajo
  carga de emisión.
- Adaptador SII sin escritura en la base (el servicio persiste) y token en caché (#13).
- Redis opcional (`REDIS_URL`); sin él, caché en memoria.

### Fase 4 — Autenticación mínima (etapa siguiente)
- Tokens de integración ligados a la cuenta del administrador, con rol `integracion`.
  Migración `0003`.

### Fase 5 — API para productos (etapa siguiente; se suma a lo existente)
- `emitir` y `previsualizar` con emisor opcional (lo toma del negocio).
- `POST /api/v1/dte/{dte_id}/enviar` (facturas por SOAP, boletas por REST: #16) y
  `GET /api/v1/dte/{dte_id}/estado`, que **guarda** el veredicto (#14).
- Regenerar `http/api/`, escribir `http/flujos/02-cliente.http` y reescribir [`INTEGRACION.md`](INTEGRACION.md).

### Fase 6 — Despliegue
`docker-compose.example.yml` con Postgres (y Redis opcional), `alembic upgrade head` como paso
explícito, respaldo con `pg_dump` + volumen `storage/` + `.env`.

## Política de folios

Un folio es el número legal del documento. Se gasta en tres momentos distintos, y cada uno tiene
una respuesta distinta:

| Dónde falla | Qué pasa con el folio | Qué hace el motor |
|---|---|---|
| **Antes de reservar** (datos inválidos) | no se toca | 422 con el detalle. Es donde deben caer casi todos los errores: la prevalidación de la fase 2 existe para eso |
| **Después de reservar, antes de enviar al SII** (XSD, firma, disco, o el producto quiere cambiar algo) | el SII no lo conoce: el documento nunca salió | la fila (`error` o `generado`) es **dueña de su folio**. El producto vuelve a enviar el mismo JSON corregido con **el mismo `id_externo`**: se regenera con el mismo folio y los datos nuevos. No hay folio nuevo ni nada que anular |
| **El SII lo rechaza** (`RCH`, `RFR`, `RCT`) | a verificar en certificación | se guarda `rechazado` con el motivo (#14). Si el SII admite reenviar con el mismo folio, la corrección con el mismo `id_externo` usa el mismo folio. Hasta confirmarlo, se trata como el caso siguiente |
| **El SII lo aceptó** | es un documento legal | no se anula el folio: se corrige con nota de crédito o débito (61/56), que gasta un folio de ese tipo. Es el proceso legal, no un error del motor |

### El `id_externo`

El producto no genera ninguna clave ni agrega cabeceras. Manda su propio identificador de la
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
| con el mismo JSON | cualquiera | 200 con el documento existente: mismo `dte_id` y folio. Es el reintento tras un timeout (#3) |
| con el JSON **cambiado** | `error` o `generado` (no enviado) | **corrección**: se regenera con el mismo folio. El XML y el PDF anteriores se conservan como versión previa, no se sobrescriben |
| con el JSON cambiado | `enviado`, `aceptado` | 409 con el `dte_id`: ya está en el SII y se corrige con nota de crédito |
| mientras otra petición la procesa | `reservado` | 409 `en_curso`, sin esperar |

- El motor compara un hash del JSON para distinguir reintento de corrección.
- Una fila `reservado` huérfana (el proceso cayó a mitad) pasa a `error` después de un plazo.
- Si el producto ya entregó el PDF al cliente final (una boleta impresa), corregirlo es decisión
  del producto: el motor lo permite mientras no se haya enviado al SII.
- Sin `id_externo` (solo la ruta actual, para la UI y el administrador), cada petición es un
  documento nuevo, como hoy.

**Folios abandonados.** Una fila en `error` que el producto nunca corrige deja un hueco en la
numeración. Para que eso no obligue a anular: la siguiente emisión del mismo tipo toma primero un
folio de una fila en `error` vencida (nunca enviada, nunca entregada; con candado en la base) y
solo si no hay ninguna avanza `folio_siguiente`. El folio no se libera si el XML o el PDF ya se
entregaron al producto. Lo que aun así quede sin usar (CAF vencido, por ejemplo) aparece en un
reporte "folios por anular", para anularlos en el portal del SII.

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
