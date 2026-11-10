# Despliegue por cliente (servidor, Docker, motor Python)

Guía para desplegar Faro como **motor de facturación detrás de tus propios sistemas** (las integraciones: punto de venta,
tienda, ERP): llaman a la API y Faro firma, timbra, envía al SII y guarda. Una instancia por
empresa cliente, con la API solo en la red interna y sin la interfaz gráfica a la vista.

| Archivo | Para qué |
|---|---|
| este `README.md` | decisiones, arquitectura, despliegue paso a paso, operación |
| [`PLAN.md`](PLAN.md) | **el plan del fork**: hallazgos verificados, decisiones y fases (migraciones, transacción e idempotencia, Postgres, autenticación) |
| [`CONVENCIONES.md`](CONVENCIONES.md) | reglas del fork: idioma (español en todo), base de datos, migraciones, API, código y cómo traer cambios de upstream |
| [`ARQUITECTURA.md`](ARQUITECTURA.md) | lo que hay medido, el estándar objetivo y los tres caminos (decidido: mejorar en el lugar) |
| [`INTEGRACION.md`](INTEGRACION.md) | la API actual tal como es hoy (alta del cliente, emitir, estado, consulta pública) |
| [`../../manual/`](../../manual/) | el manual para quien consume la API (integraciones y soporte), con diagramas; se publica en GitHub Pages |
| [`POSTGRESQL.md`](POSTGRESQL.md) | SQLite y PostgreSQL con SQLAlchemy Core + Alembic: cómo se usa, cómo está armado y su verificación |
| [`schema.dbml`](schema.dbml) | tablas y relaciones (generado desde las migraciones; se ve en dbdiagram.io) |
| [`../../http/`](../../http/README.md) | llamadas a la API para la extensión REST Client de VS Code |
| [`../../docker-compose.prod.yml`](../../docker-compose.prod.yml) | el compose de **una instancia de cliente**: Faro, PostgreSQL y la migración |
| [`cliente.env.example`](cliente.env.example) | el `.env` de una instancia de cliente |
| [`../../tools/respaldo.sh`](../../tools/respaldo.sh) | respaldo y restauración de una instancia |
| [`../../docker-compose.dev.yml`](../../docker-compose.dev.yml) | desarrollo local: PostgreSQL, interfaz y Swagger en localhost (SQLite comentado) |

> Faro es **beta** (P5 de [`CONSTITUCION.md`](../CONSTITUCION.md)). Antes de emitir documentos
> reales lee las leyes L1 (certificación vs producción) y L3 (`EPR` no significa aceptado).

## Decisiones

| Tema | Decisión | Por qué |
|---|---|---|
| Motor | **Python** (`Dockerfile`, `python:3.12-slim`) | Es la referencia del proyecto y trae Swagger (`/docs`) (`docs/COMPARACION-PYTHON-GO.md`). El binario Rust (`nucleo-servidor servir`) tiene el mismo contrato de API y es el destino final del port, pero no tiene imagen Docker oficial. |
| Aislamiento | **Una instancia por empresa** (carpeta, contenedores, volúmenes y `.env` propios) | Faro no es multi-tenant: usuarios y API keys no se separan por empresa. Una instancia por cliente aísla datos, certificados y secretos. |
| Base de datos | **PostgreSQL** en producción, SQLite en local y pruebas | Las dos con el mismo código y el esquema en migraciones de Alembic (fase 1 de [`PLAN.md`](PLAN.md)). |
| Imagen | **Se construye en el VPS** desde el repositorio, una vez por versión (`faro-dte:<versión>`) | Sin registro de imágenes por ahora. Corre como usuario `faro` (UID 10001), sin `.env` adentro: la configuración llega por `env_file`. |
| Procesos | **Un proceso de uvicorn por instancia** | Alcanza para el volumen de una empresa. El RVD y la reposición de folios corren dentro del proceso y no tienen candado entre procesos: con varios, cada uno los correría. Redis queda comentado en el compose para cuando haga falta. |
| Exposición | **Nada publica puertos.** La API va en la red `integraciones`; la base, en una red interna sin salida | `docs/PLATAFORMA.md` §5: no exponer la API REST a Internet. Tus integraciones la llaman por la red de Docker. |
| Interfaz gráfica | **No se publica** | Las páginas (`/app`, `/docs`, `/onboarding`, `/apikeys`…) existen dentro del contenedor, pero sin puerto nadie de afuera llega a ellas. Para aprender, usa la instancia local. |
| Buscador público | Lo sirve **tu integración** con `/consulta/api` | Este despliegue no publica ninguna página. Ver [`INTEGRACION.md`](INTEGRACION.md#consulta-pública-de-boletas). El motor conserva la opción de publicar su propio buscador por un proxy (`DOMINIO_PUBLICO_BOLETAS`, middleware de `main.py`), sin uso aquí. |
| Respaldos | **`tools/respaldo.sh`**: base, volumen y `.env` en un archivo, con retención | Por ahora sin almacenamiento externo (S3): copiar el archivo fuera del servidor queda a cargo de quien opera. |
| MCP | **Fuera** de este despliegue | `mcp_server.py` es un proceso aparte y no va en la imagen. |

## Arquitectura

```
   VPS ─────────────────────────────────────────────────────────────────────────┐
   │                                                                           │
   │  red `integraciones` (compartida)                                         │
   │   ┌──────────────┐   http://faro-acme:8000/api/v1/...                     │
   │   │ integración  ├──────────────────┐                                     │
   │   │ (backend)    │                  ▼                                     │
   │   └──────────────┘        ┌──────────────────┐                            │
   │                           │ faro (uvicorn,   │──────────────────────────────┼──► SII
   │                           │ 1 proceso)       │                            │   (maullín / palena)
   │                           │ volumen: storage │                            │
   │                           └────────┬─────────┘                            │
   │  red `faro-acme_interna`           │ (sin salida a Internet)              │
   │                           ┌────────┴─────────┐   ┌──────────────────┐     │
   │                           │ db (PostgreSQL)  │   │ migrar (una vez) │     │
   │                           │ volumen: pg-data │   │ alembic upgrade  │     │
   │                           └──────────────────┘   └──────────────────┘     │
   └───────────────────────────────────────────────────────────────────────────┘
```

Cada cliente tiene su carpeta (`/srv/clientes/<cliente>`), su proyecto de Compose
(`faro-<cliente>`) y su red interna. En la red `integraciones` Faro responde con el nombre del
proyecto (`faro-acme`). La base no es visible desde esa red. Solo el backend de cada integración
conoce su token.

Al levantar, Compose arranca `db`; cuando está sana corre `migrar` (`alembic upgrade head`), y
solo si `migrar` termina bien arranca `faro`. Una migración que falla deja Faro detenido, no a
medio subir.

## Local: para aprender

```bash
cp .env.example .env                              # y define ADMIN_PASSWORD
docker compose -f docker-compose.dev.yml up -d --build
```

- `http://localhost:8000/app`: la misma interfaz de la app Faro. Entra con `admin` y tu
  `ADMIN_PASSWORD`. Practica con EMPRESA DEMO SPA: firma y timbra de verdad, pero nunca envía al SII.
- `http://localhost:8000/docs`: Swagger, para probar cada endpoint (botón **Authorize**).
- `docker compose -f docker-compose.dev.yml down -v` borra la instancia local y vuelve a cero.
- Para no escribir `-f`: `COMPOSE_FILE=docker-compose.dev.yml` en tu `.env`.
- El compose de desarrollo usa PostgreSQL, como producción (base en `127.0.0.1:5432`, usuario
  `faro`, clave `POSTGRES_PASSWORD` de tu `.env` o `faro-local`). SQLite queda comentado. El
  manual de la API va como perfil (`--profile manual`, en `http://127.0.0.1:8001`).

Desde la fase 6 la imagen corre sin privilegios. Un volumen creado con una imagen anterior (que
corría como root) se corrige una vez:

```bash
docker compose -f docker-compose.dev.yml run --rm --user 0 faro chown -R faro:faro /app/storage /app/logs
```

La práctica funciona sin configurar nada más. Para probar contra el SII de certificación
(Maullín) necesitas el certificado `.pfx` del representante legal, el RUT de la empresa y folios
(CAF). Se cargan por la API ([`INTEGRACION.md`](INTEGRACION.md)), no en el `.env`.

## Desplegar un cliente

Ejemplo con el cliente `acme`. El repositorio vive en `/srv/faro-dte` y cada cliente en
`/srv/clientes/<cliente>`.

**1. Servidor (una sola vez).** Docker con el plugin Compose v2, el repositorio y la red
compartida con tus integraciones:

```bash
git clone https://github.com/rashArt/faro-dte.git /srv/faro-dte
git -C /srv/faro-dte checkout servidor-produccion
docker network create integraciones
```

**2. Carpeta del cliente.**

```bash
mkdir -p /srv/clientes/acme && cd /srv/clientes/acme
cp /srv/faro-dte/docker-compose.prod.yml docker-compose.yml
cp /srv/faro-dte/docs/deploy/cliente.env.example .env
chmod 600 .env
```

**3. `.env`.** Completa los valores entre `<>`. Los secretos son distintos por cliente:

```bash
openssl rand -hex 24                                  # POSTGRES_PASSWORD (solo letras y números)
openssl rand -hex 32                                  # JWT_SECRET_KEY
openssl rand -base64 32 | tr '+/' '-_'                # DTE_MASTER_KEY (clave Fernet)
openssl rand -base64 18                               # ADMIN_PASSWORD
```

| Variable | Valor para `acme` |
|---|---|
| `COMPOSE_PROJECT_NAME` | `faro-acme`: prefijo de contenedores y volúmenes, y nombre de la API en la red (`http://faro-acme:8000`) |
| `FARO_VERSION` | la versión que vas a construir, por ejemplo la etiqueta de git |
| `FARO_FUENTE` | `/srv/faro-dte` |
| `SII_AMBIENTE` | `certificacion` hasta terminar la certificación; después `produccion` (L1) |
| `ENVIO_SII` | `activo`. Con `desactivado` se emite pero nada llega al SII |

El compose no levanta si falta `COMPOSE_PROJECT_NAME`, `FARO_VERSION`, `POSTGRES_PASSWORD`,
`SII_AMBIENTE` o `ENVIO_SII`: son decisiones explícitas de cada cliente.

Guarda `DTE_MASTER_KEY` también **fuera del servidor** (gestor de secretos). Cifra los
certificados guardados; si se pierde, hay que volver a subirlos.

**4. Construir y levantar.**

```bash
docker compose build          # faro-dte:<FARO_VERSION>, desde /srv/faro-dte
docker compose up -d --wait faro
```

Si otro cliente ya construyó la misma versión, `build` reutiliza las capas en caché y termina en
segundos.

**5. Comprobar.**

```bash
docker compose ps -a                                  # db y faro "healthy"; migrar "Exited (0)"
docker compose logs faro | grep -E "Ambiente SII|Base de datos|SEGURIDAD|Envío"
docker run --rm --network integraciones curlimages/curl -s http://faro-acme:8000/health
```

`Base de datos` tiene que decir `postgresql+psycopg://faro:***@db:5432/faro`. Con
`SII_AMBIENTE=produccion` el motor **no arranca** si queda algún aviso `SEGURIDAD`.

**6. Alta del cliente y API key.** Sigue [`INTEGRACION.md`](INTEGRACION.md) o el
[manual](../../manual/docs/guias/configurar-empresa.md): certificado, negocio, datos del emisor,
folios y un token para cada integración. Sin puertos publicados, esas llamadas se hacen desde la
red `integraciones` (o con un túnel SSH a un contenedor de esa red).

**7. Primer respaldo.** `/srv/faro-dte/tools/respaldo.sh respaldar` (ver [Respaldos](#respaldos)).

## Operación

### Respaldos

Desde la carpeta del cliente:

```bash
cd /srv/clientes/acme
/srv/faro-dte/tools/respaldo.sh respaldar
```

Deja `respaldos/faro-acme-<fecha>.tar.gz` con la base (`pg_dump`), el volumen `storage/` (XML
firmados y PDF), el `.env`, el `docker-compose.yml` y un `MANIFIESTO.txt` (versión, migración de
Alembic, conteos). Antes de guardarlo comprueba que los dos volcados se puedan leer. Borra los
respaldos de más de 30 días (`RETENCION_DIAS`, `0` para no borrar).

Programado cada noche:

```cron
0 3 * * * cd /srv/clientes/acme && /srv/faro-dte/tools/respaldo.sh respaldar >> respaldos/respaldo.log 2>&1
```

> **El respaldo incluye el `.env`, y con él `DTE_MASTER_KEY`:** quien tenga el archivo puede
> descifrar los certificados del cliente. Guárdalo protegido. Un respaldo que vive solo en el VPS
> se pierde con el VPS: cópialo fuera (por ejemplo con `scp` o `rsync` a otro equipo).

El SII exige conservar los documentos emitidos. Prueba la restauración, no solo la copia.

### Restaurar

En el mismo servidor o en uno nuevo, desde la carpeta del cliente:

```bash
mkdir -p /srv/clientes/acme && cd /srv/clientes/acme
/srv/faro-dte/tools/respaldo.sh restaurar /ruta/faro-acme-<fecha>.tar.gz
```

- En una carpeta vacía toma el `.env` y el `docker-compose.yml` del respaldo.
- Si ya hay un `.env` con otra `DTE_MASTER_KEY`, no sigue: los certificados no se descifrarían.
- Pide escribir el nombre del proyecto para confirmar (`--si` lo omite).
- Si la base tiene datos, primero respalda lo que hay (`…-antes-de-restaurar.tar.gz`).
- Restaura la base y el volumen y levanta la instancia. `migrar` corre antes de Faro, así que un
  respaldo de una versión anterior queda al día.

### Actualizar

```bash
git -C /srv/faro-dte pull
cd /srv/clientes/acme
/srv/faro-dte/tools/respaldo.sh respaldar
# en .env: FARO_VERSION=<versión nueva>
docker compose build
docker compose up -d --wait faro       # migrar aplica las migraciones nuevas antes de arrancar
docker compose ps -a
```

`docker compose run --rm migrar alembic current` dice en qué migración está la base.

**Volver a la versión anterior.** La imagen anterior sigue en el servidor (`docker image ls
faro-dte`). Una migración nueva puede haber cambiado la base, así que se vuelve con el respaldo
tomado antes de actualizar:

```bash
# en .env: FARO_VERSION=<versión anterior>
/srv/faro-dte/tools/respaldo.sh restaurar respaldos/faro-acme-<fecha de antes de actualizar>.tar.gz
```

Lo emitido entre la actualización y la vuelta atrás queda en el respaldo `…-antes-de-restaurar`:
esos folios ya están timbrados, no se reemiten.

### Monitoreo mínimo

| Qué | Cómo | Cuándo |
|---|---|---|
| Contenedores sanos | `docker compose ps -a`: `db` y `faro` en `healthy` (el healthcheck llama a `/health`) | después de cada cambio; en tu monitor de servidor |
| Documentos que necesitan a una persona | `GET /api/v1/dte/alertas` (administrador): `sin_veredicto`, `envio_fallido`, `folios_por_anular` | cada día |
| Folios | `NOTIF_WEBHOOK_URL` recibe `folios_repuesto`, `folios_bloqueado` y `folios_error` | conéctalo a tu sistema de alertas |
| Registros | `docker compose logs --since 24h faro` (rotan solos: 5 archivos de 10 MB por contenedor) | ante un error; busca el `request_id` que cita la integración |
| Respaldos | que `respaldos/` tenga el archivo de la noche y `respaldo.log` no muestre `ERROR` | cada semana |

### Más operación

- **Un solo proceso por instancia.** No agregues `--workers` ni réplicas: el RVD diario y la
  reposición automática de folios (`core/scheduler.py`, `core/folios_auto.py`) correrían en cada
  proceso. El despacho al SII sí admite varios (reserva cada documento), pero no es el cuello.
- **Rotar secretos.** Si solo cambias `DTE_MASTER_KEY` en el `.env`, los certificados
  guardados ya no se descifran. `rotar_claves.py` vuelve a cifrar el keystore y escribe los
  secretos nuevos en el `.env` (también resetea la clave de `admin` y deja `CORS_ORIGINS` en
  localhost). En una ventana de mantenimiento: respalda,
  `docker compose run --rm --no-deps --user 0 -v ./.env:/app/.env faro python rotar_claves.py`,
  revisa el `.env` y `docker compose up -d --force-recreate faro`. *No verificado todavía con
  PostgreSQL.*
- **Ambiente.** `SII_AMBIENTE=certificacion` hasta terminar la certificación del cliente
  ([`../CERTIFICACION.md`](../CERTIFICACION.md)). Después cambia a `produccion` (L1) y recrea
  Faro (`docker compose up -d faro`).
- **Administrar la base.** No publica puertos. Para DBeaver o pgAdmin, un túnel SSH al
  contenedor `db` ([`POSTGRESQL.md`](POSTGRESQL.md)), o `docker compose exec db psql -U faro faro`.

## Verificación del despliegue (2026-11-10)

Simulacro en Docker Desktop (Windows) con la instancia `faro-sim`, armada exactamente como en
[Desplegar un cliente](#desplegar-un-cliente): `docker-compose.prod.yml` como `docker-compose.yml`,
`cliente.env.example` con secretos generados, `SII_AMBIENTE=certificacion`,
`ENVIO_SII=desactivado` y el RVD y la reposición de folios apagados. Nada salió al SII: el
certificado y los CAF son los ficticios de `tests/dorado/fixtures`. Las llamadas las hizo un
contenedor en la red `integraciones`, como una integración.

| Prueba | Resultado |
|---|---|
| `docker compose up -d --wait faro`: db sana, `migrar` aplica 0001→0004 y termina, Faro sano | ✅ |
| Faro corre como UID 10001, no escribe en `/app`, no hay `.env` en la imagen | ✅ |
| `faro-sim:8000` responde desde la red `integraciones`; `db` no se resuelve desde ella; la base no sale a Internet; Faro sí (para el SII); ningún puerto publicado | ✅ |
| Falta `ENVIO_SII` en el `.env`: el compose no levanta y dice qué definir | ✅ |
| Alta: certificado al keystore, CAF 33 y 39, API key de integración; emisión de una factura y una boleta con `id_externo`, reintento `repetido`, PDF y estado | ✅ |
| `respaldo.sh respaldar` (96 KB) → `docker compose down -v` y borrar la imagen y la carpeta → carpeta nueva con solo el archivo → `respaldo.sh restaurar` (29 s, reconstruye la imagen) | ✅ |
| Tras restaurar: mismo admin, la API key sigue valiendo, 2 documentos, PDF idénticos byte a byte (SHA-256), el reintento devuelve el mismo folio, el certificado del keystore se descifra (`previsualizar` con `cert_id`) y la emisión nueva toma el folio 2 | ✅ |
| Restaurar sobre una base con datos deja antes `…-antes-de-restaurar.tar.gz`; con otra `DTE_MASTER_KEY` se niega; sin confirmación, cancela | ✅ |
| Memoria en reposo: Faro 100 MiB, PostgreSQL 35 MiB (límite 1 GiB cada uno) | medido |
| Imagen con SQLite por defecto en `storage/` (CI: `docker run` sin `DATABASE_URL`): `/health` 200; corpus dorado 15/15 dentro de la imagen | ✅ |

Sin verificar aquí: un VPS real, el envío al SII de certificación con un CAF y un certificado
reales, y `rotar_claves.py` con PostgreSQL.

## Hallazgos de la verificación (2026-10-15)

Los de integridad de la emisión (duplicados, folios, registro abierto) están en
[`PLAN.md`](PLAN.md#verificación-2026-10-15-imagen-faro-dtelocal-sqlite). Los del despliegue,
comprobados al levantar la imagen en limpio:

| Hallazgo | Estado |
|---|---|
| La base quedaba en `/app/dte_database.db`, fuera del volumen: al recrear el contenedor se perdían usuarios, certificados, CAF, folios y documentos (en el log volvía a salir "USUARIO ADMIN CREADO"). | **Corregido**: el compose de desarrollo fija `DATABASE_URL=sqlite:////app/storage/dte_database.db`, y desde la fase 6 también es el valor por defecto de la imagen. Producción usa PostgreSQL. |
| `ADMIN_PASSWORD` en el `.env` no llegaba al proceso (`main.py` la lee del entorno, y el compose solo montaba el archivo). | **Corregido**: `env_file: .env`. |
| Si falta `./.env`, Docker crea un directorio en su lugar y el arranque falla. | Documentado: `cp .env.example .env` antes de levantar (desarrollo). El compose de producción ya no monta el archivo. |
| PostgreSQL no estaba soportado (el `.env.example` decía que sí). | **Soportado** desde la fase 1, con migraciones de Alembic ([`POSTGRESQL.md`](POSTGRESQL.md)). |
| En Python, `POST /api/v1/dte/emitir` y `/api/v1/estado/*` no leen el certificado del keystore: lo piden en el cuerpo o en `CERTIFICADO_PATH`. Go y Rust aceptan `cert_id`. | **`emitir` corregido** en la fase 2 (toma el certificado del negocio). `/estado/track` sigue pidiéndolo en el cuerpo. |
