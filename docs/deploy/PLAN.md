# Plan del fork: Faro como API de facturación para productos

> **Estado (2026-10-15):** plan aprobado; nada implementado salvo lo marcado ✅. Rama de
> trabajo: `servidor-produccion`. Los hallazgos están **verificados en vivo** contra la imagen
> Docker del motor Python (`tools/sondeo_integridad.py`).
>
> **Decidido:** se mejora **en el lugar**, sin paquete ni proyecto nuevo. El motor ya funciona en
> producción y otra base de código exigiría volver a probar contra el SII real. El estándar de
> [`ARQUITECTURA.md`](ARQUITECTURA.md) se aplica a cada parte que se toca. Alcance de esta etapa:
> **base de datos y migraciones, transacciones y despliegue**. Go, Rust, las apps y el MCP quedan
> intactos para poder traer cambios del repositorio original.

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
| 9 | Imagen construida desde Windows | `autocrlf` → corpus dorado 0/15; con LF, **15/15** | media |
| 10 | N+1 | **No hay** en la API: listados paginados, ~5 consultas por emisión | — |
| 11 | Dependencias sin fijar | `mcp` 2.x rompe `mcp_server.py` | media |
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
| Idempotencia | `Idempotency-Key` obligatoria al emitir; la verdad está en la base, en la misma transacción que el folio |
| Autenticación | un **administrador** (JWT) y **tokens de integración** por producto, ligados a su cuenta, con rol `integracion`: emitir, enviar, consultar. Gestión (keystore, CAF, tokens) solo el administrador. Registro público cerrado |
| Superficie | **no se corta nada**: las rutas actuales siguen montadas; las nuevas se suman. El emisor y el certificado pasan a ser opcionales (los completa el negocio de la instancia) |
| Caché | Redis opcional: token SII, resolución, límite de login y candado de líder. Nunca folios ni idempotencia |

## Fases

Cada fase cierra con pruebas en verde en **SQLite y Postgres** (desde la fase 1) y `test_dorado`
15/15. **Esta etapa: fases 0, 1, 2 y 6.** Las fases 3, 4 y 5 son la etapa siguiente, salvo lo
que la fase 2 adelanta.

### Fase 0 — Base de trabajo
- ✅ `http/` con REST Client (80 operaciones generadas por `tools/generar_http.py` + flujos a mano).
- ✅ `tools/sondeo_integridad.py` (reproduce #1, #3, #4, #5).
- ✅ Diagnóstico de arquitectura ([`ARQUITECTURA.md`](ARQUITECTURA.md)).
- **Reubicar los 13 `test_*.py` de la raíz** en `tests/scripts/`. Se corrigen sus rutas
  internas y las 24 referencias (CI `.github/workflows/dorado.yml`, `tools/prueba_final.sh`,
  docs, agentes). La condición: los que pasan hoy siguen pasando (`test_dorado`,
  `test_simulacion`, `test_mvp`, `test_mipyme`). Los que dependen del Llavero de macOS quedan
  marcados como tales.
- pytest + `TestClient`: fixture de instancia limpia (base temporal, CAF y certificado ficticios).
  Los hallazgos se escriben como **pruebas en rojo**.
- `.gitattributes` (LF para `*.xml`, `*.json`, `*.sh`, fixtures), dependencias fijadas, imagen en CI.

### Fase 1 — Datos y migraciones (prioridad)
Ver [`POSTGRESQL.md`](POSTGRESQL.md).
- `Engine` de SQLAlchemy detrás de `core/database.py`, sin cambio de comportamiento.
- Alembic `0001_esquema_inicial` (13 tablas) para ambos dialectos; los `init_*` dejan de crear
  tablas. `alembic upgrade head` en el despliegue; `downgrade` probado.
- Postgres (psycopg 3), folio con `SELECT … FOR UPDATE`, compose local con Postgres y pgAdmin
  opcional.
- Prueba de **actualización**: base en la versión N con datos → `upgrade` → la suite pasa.

### Fase 2 — Emisión transaccional e idempotencia (lo más importante)
- Servicio `EmitirDocumento`: reserva `Idempotency-Key`, folio y fila `reservado` en **una**
  transacción. Después firma, validación XSD (facturas y boletas), archivos por `dte_id` sin
  sobrescribir y estado `generado`. Si falla, la fila queda en `error` con el motivo y el
  reintento con la misma clave reutiliza el folio (#3, #5, #6).
- Folio explícito solo para el administrador, validado contra `folio_siguiente` (#4).
- `IntegrityError` → 409 (#7). Referencias obligatorias en 56/61 (#15).
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

## Riesgos

- **Firma intocable (L2, L15):** ninguna fase toca `core/sobre.py` ni la generación del XML.
  `test_dorado` es gate de cada fase.
- **Folios reales (L1, L7, L10):** las fases 1 y 2 cambian cómo se consume un folio. Se prueban
  con el CAF ficticio y en certificación antes de producción.
- **Distancia con upstream:** cada cambio en `core/database.py`, `core/models.py` y los
  orquestadores puede chocar al traer cambios del repositorio original. Los cambios se
  concentran en esos archivos y se mantienen chicos. La firma (`core/sobre.py`, `core/dte.py`)
  no se toca.
