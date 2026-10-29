# Despliegue por cliente (servidor, Docker, motor Python)

Guía para desplegar Faro como **motor de facturación detrás de tus propios productos**: tus
productos llaman a la API y Faro firma, timbra, envía al SII y guarda. Una instancia por
empresa cliente, con la API solo en la red interna y sin la interfaz gráfica a la vista.

| Archivo | Para qué |
|---|---|
| este `README.md` | decisiones, arquitectura, despliegue paso a paso, operación |
| [`PLAN.md`](PLAN.md) | **el plan del fork**: hallazgos verificados, decisiones y fases (migraciones, transacción e idempotencia, Postgres, autenticación) |
| [`CONVENCIONES.md`](CONVENCIONES.md) | reglas del fork: idioma (español en todo), base de datos, migraciones, API, código y cómo traer cambios de upstream |
| [`ARQUITECTURA.md`](ARQUITECTURA.md) | lo que hay medido, el estándar objetivo y los tres caminos (recomendado: cascarón nuevo, mismo corazón SII) |
| [`INTEGRACION.md`](INTEGRACION.md) | la API actual tal como es hoy (alta del cliente, emitir, estado, consulta pública) |
| [`POSTGRESQL.md`](POSTGRESQL.md) | SQLite y PostgreSQL con SQLAlchemy Core + Alembic (hoy **no** está soportado) |
| [`../../http/`](../../http/README.md) | llamadas a la API para la extensión REST Client de VS Code |
| [`cliente.env.example`](cliente.env.example) | `.env` de una instancia de cliente |
| [`../../docker-compose.example.yml`](../../docker-compose.example.yml) | el compose base que se copia en cada despliegue |

> Faro es **beta** (P5 de [`CONSTITUCION.md`](../CONSTITUCION.md)). Antes de emitir documentos
> reales lee las leyes L1 (certificación vs producción) y L3 (`EPR` no significa aceptado).

## Decisiones

| Tema | Decisión | Por qué |
|---|---|---|
| Motor | **Python** (`Dockerfile`, `python:3.12-slim`) | Es la referencia del proyecto, trae Swagger (`/docs`) y es la imagen que el autor usa en su propio servidor (`docs/COMPARACION-PYTHON-GO.md`). El binario Rust (`nucleo-servidor servir`) tiene el mismo contrato de API y es el destino final del port, pero no tiene imagen Docker oficial. |
| Aislamiento | **Una instancia por empresa** (contenedor, volumen y `.env` propios) | Faro no es multi-tenant: usuarios y API keys no se separan por empresa. Una instancia por cliente aísla datos, certificados y secretos. |
| Exposición | **API solo en la red de Docker**; nada público por defecto | `docs/PLATAFORMA.md` §5: no exponer la API REST a Internet. Tus productos la llaman por la red interna. |
| Interfaz gráfica | **No se publica** | Las páginas (`/app`, `/docs`, `/onboarding`, `/apikeys`…) existen dentro del contenedor, pero sin puerto ni ruta de Traefik nadie de afuera llega a ellas. Para aprender, usa la instancia local. |
| Buscador público | Lo sirve **tu producto** con `/consulta/api`; opcional por Traefik | Ver [`INTEGRACION.md`](INTEGRACION.md#consulta-pública-de-boletas). |
| Base de datos | **PostgreSQL** en producción, SQLite en local y pruebas | Hoy solo funciona SQLite. La fase 1 de [`PLAN.md`](PLAN.md) agrega Postgres y migraciones sin perder SQLite. |
| MCP | **Fuera** de este despliegue | `mcp_server.py` es un proceso aparte y no va en la imagen. |

## Arquitectura

```
                    Internet
                       │  (443)
                  ┌────┴─────┐
                  │ Traefik  │   solo si publicas el buscador de Faro (opcional)
                  └────┬─────┘
                       │ Host(boletas.cliente.ejemplo.cl) → solo /consulta*
   ┌───────────────────┼──────────────────────────────────────────┐
   │ red Docker        │                                          │
   │  ┌──────────────┐ │  http://faro-acme:8000/api/v1/...        │
   │  │ tu producto  ├─┼──────────────► ┌──────────────────┐      │
   │  │ (backend)    │ │                │ faro-acme        │──────┼──► SII (maullín / palena)
   │  └──────────────┘ └──────────────► │ uvicorn 1 worker │      │
   │                                    │ volumen: storage │      │
   │                                    └──────────────────┘      │
   └──────────────────────────────────────────────────────────────┘
```

Cada cliente tiene su contenedor `faro-<cliente>` en una red compartida con tus productos.
Solo el backend de tu producto conoce la API key.

## Local: para aprender

```bash
cp .env.example .env                              # y define ADMIN_PASSWORD
cp docker-compose.example.yml docker-compose.yml  # descomenta `ports: 127.0.0.1:8000:8000`
docker compose up -d --build
```

- `http://localhost:8000/app`: la misma interfaz de la app Faro. Entra con `admin` y tu
  `ADMIN_PASSWORD`. Practica con EMPRESA DEMO SPA: firma y timbra de verdad, pero nunca envía al SII.
- `http://localhost:8000/docs`: Swagger, para probar cada endpoint (botón **Authorize**).
- `docker compose down -v` borra la instancia local y vuelve a cero.

La práctica funciona sin configurar nada más. Para probar contra el SII de certificación
(Maullín) necesitas el certificado `.pfx` del representante legal, el RUT de la empresa y folios
(CAF). Se cargan por la API ([`INTEGRACION.md`](INTEGRACION.md)), no en el `.env`.

## Desplegar un cliente

Ejemplo con el cliente `acme` y una red compartida `productos`, donde también viven tus productos.

**1. Imagen.** Constrúyela una vez por versión y etiquétala. No compiles en cada cliente.

```bash
git -C /srv/faro-dte pull
docker build -t faro-dte:1.1.0 /srv/faro-dte
docker network create productos   # una sola vez por servidor
```

**2. Carpeta del cliente.**

```bash
mkdir -p /srv/clientes/acme && cd /srv/clientes/acme
cp /srv/faro-dte/docs/deploy/cliente.env.example .env
cp /srv/faro-dte/docker-compose.example.yml docker-compose.yml
chmod 600 .env
```

**3. `.env`.** Genera los secretos (son distintos por cliente):

```bash
openssl rand -hex 32                                  # JWT_SECRET_KEY
openssl rand -base64 32 | tr '+/' '-_'                # DTE_MASTER_KEY (clave Fernet)
openssl rand -base64 18                               # ADMIN_PASSWORD
```

Guarda `DTE_MASTER_KEY` también **fuera del servidor** (gestor de secretos). Cifra los
certificados guardados; si se pierde, hay que volver a subirlos.

**4. `docker-compose.yml` del cliente.** Sobre la copia del ejemplo:

```yaml
services:
  faro:
    image: faro-dte:1.1.0          # en vez de `build:`
    container_name: faro-acme      # nombre que usan tus productos
    # ... (lo demás igual al ejemplo)
    networks: [productos]

networks:
  productos:
    external: true
```

Quita el bloque `build:` y no publiques `ports`.

**5. Levantar y comprobar.**

```bash
docker compose -p faro-acme up -d
docker compose -p faro-acme logs faro | grep -E "Ambiente SII|Base de datos|SEGURIDAD"
docker run --rm --network productos curlimages/curl -s http://faro-acme:8000/health
```

`Base de datos` tiene que decir `sqlite:////app/storage/dte_database.db`. Con
`SII_AMBIENTE=produccion` el motor **no arranca** si queda algún aviso `SEGURIDAD`.

**6. Alta del cliente y API key.** Sigue [`INTEGRACION.md`](INTEGRACION.md): certificado,
negocio, diagnóstico, folios y una API key para cada producto.

### Publicar el buscador de Faro con Traefik (opcional)

Solo si no lo va a servir tu producto. En el `.env` del cliente:
`DOMINIO_PUBLICO_BOLETAS=boletas.cliente.ejemplo.cl`. En el compose, descomenta las `labels` y
la red `traefik` del ejemplo con ese mismo host, y agrega `FORWARDED_ALLOW_IPS=*` a
`environment` para que uvicorn confíe en las cabeceras de Traefik. El middleware de `main.py`
deja ver **solo** `/consulta*` en ese host. Todo lo demás responde 404, incluidos `/app`,
`/docs` y la API.

Nunca enrutes otro host hacia el contenedor: Traefik no agrega autenticación, y las páginas y
`/docs` quedarían expuestas.

## Operación

- **Un solo proceso por instancia.** El RVD diario y la reposición automática de folios corren
  dentro de uvicorn (`core/scheduler.py`, `core/folios_auto.py`). No agregues `--workers` ni
  réplicas: se duplicarían los envíos al SII, y SQLite admite un solo escritor.
- **Respaldos.** Copia en caliente de la base (API de respaldo de SQLite) más el volumen
  (XML firmados y PDF) y el `.env`:

  ```bash
  docker compose -p faro-acme exec faro python -c "import sqlite3; s=sqlite3.connect('/app/storage/dte_database.db'); d=sqlite3.connect('/app/storage/respaldo.db'); s.backup(d); d.close()"
  docker run --rm -v faro-acme_faro-data:/datos -v "$PWD":/destino alpine tar czf /destino/acme-$(date +%F).tgz -C /datos .
  ```

  El SII exige conservar los documentos emitidos. Prueba la restauración, no solo la copia.
- **Actualizar.** `git pull`, construir `faro-dte:<versión nueva>`, cambiar la etiqueta en el
  compose del cliente y `docker compose -p faro-acme up -d`. El esquema se completa solo al
  arrancar (`init_db`, `CREATE TABLE IF NOT EXISTS` y columnas nuevas en `core/negocios.py`).
  Respalda antes.
- **Rotar secretos.** Si solo cambias `DTE_MASTER_KEY` en el `.env`, los certificados
  guardados ya no se descifran. `rotar_claves.py` hace la rotación segura: vuelve a cifrar el
  keystore y escribe los secretos nuevos en el `.env`. También resetea la clave de `admin` y deja
  `CORS_ORIGINS` en localhost. En Docker el `.env` está montado de solo lectura y el `env_file`
  se lee al crear el contenedor. Hazlo en una ventana de mantenimiento: respalda, monta el
  `.env` con escritura, corre el script, revisa el `.env` y recrea el contenedor.
- **Notificaciones.** `NOTIF_WEBHOOK_URL` recibe los eventos de folios (`folios_bloqueado`,
  `folios_error`). Conéctalo a tu sistema de alertas.
- **Ambiente.** `SII_AMBIENTE=certificacion` hasta terminar la certificación del cliente
  ([`../CERTIFICACION.md`](../CERTIFICACION.md)). Después cambia a `produccion` (L1).

## Hallazgos de la verificación (2026-10-15)

Los de integridad de la emisión (duplicados, folios, registro abierto) están en
[`PLAN.md`](PLAN.md#verificación-2026-10-15-imagen-faro-dtelocal-sqlite). Los del despliegue,
comprobados al levantar la imagen en limpio:

| Hallazgo | Estado |
|---|---|
| La base quedaba en `/app/dte_database.db`, fuera del volumen: al recrear el contenedor se perdían usuarios, certificados, CAF, folios y documentos (en el log volvía a salir "USUARIO ADMIN CREADO"). | **Corregido**: `DATABASE_URL=sqlite:////app/storage/dte_database.db` en el compose. Comprobado: tras `--force-recreate`, el log dice "Usuarios registrados: 1". |
| `ADMIN_PASSWORD` en el `.env` no llegaba al proceso (`main.py` la lee del entorno, y el compose solo montaba el archivo). | **Corregido**: `env_file: .env`. |
| Si falta `./.env`, Docker crea un directorio en su lugar y el arranque falla. | Documentado: `cp .env.example .env` antes de levantar. |
| PostgreSQL no está soportado (el `.env.example` decía que sí). | Corregido el comentario. Plan en [`POSTGRESQL.md`](POSTGRESQL.md). |
| En Python, `POST /api/v1/dte/emitir` y `/api/v1/estado/*` no leen el certificado del keystore: lo piden en el cuerpo o en `CERTIFICADO_PATH`. Go y Rust aceptan `cert_id`. | **Abierto**. Ver [`INTEGRACION.md`](INTEGRACION.md#el-certificado-al-emitir-brecha-abierta). |
