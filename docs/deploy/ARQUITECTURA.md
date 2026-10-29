# Arquitectura del fork: qué hay, qué estándar seguimos y cómo llegamos

> **Estado (2026-10-15): decidido mejorar en el lugar** (camino A con el estándar de abajo), no un
> paquete nuevo: el motor ya funciona en producción, y otra base de código exigiría volver a
> probar contra el SII real. Go, Rust y las apps se quedan intactos. Lo que sigue queda como
> registro del análisis. Mediciones contra la
> imagen `faro-dte:local`; los números de hallazgo (#) son los de [`PLAN.md`](PLAN.md).
> Recorrido de un comprobante en gráfico: artifact "Recorrido del comprobante".

## Por qué este documento va primero

Las fugas de [`PLAN.md`](PLAN.md) (duplicados, folio fuera de la transacción, event loop
bloqueado) no son descuidos aislados. Vienen de cómo se armó el motor: por necesidad, caso a
caso, para un solo usuario y una app de escritorio. Si se parchean una por una sobre esa forma,
el resultado es un monstruo difícil de mantener. Primero se fija el estándar; después cada
corrección se escribe ya dentro de él.

## Lo que hay hoy, medido

| Área | Hoy | Problema concreto |
|---|---|---|
| Capa web | FastAPI. 15 routers y 72 rutas `async def` | Solo 2 routers (`simulacion`, `mipyme`) usan `run_in_threadpool`. El resto firma, escribe disco y llama al SII **dentro del event loop**: con 8 emisiones en paralelo, `/health` pasó de 19 ms a 1.533 ms (#12). |
| Casos de uso | `OrquestadorDTE` / `OrquestadorBoleta` en `core/`. Hay SQL suelto en algunas rutas (`dte.py`, `consulta.py`) | No hay capa de servicios con un límite transaccional claro. |
| Datos | `sqlite3` + SQL a mano en 20 archivos, una conexión por llamada, esquema creado al arrancar en 7 módulos | Sin unidad de trabajo: el folio se confirma en una transacción y la fila en otra (#6). Sin migraciones ni Postgres. |
| Efectos laterales | `core/sii.py` y `core/sii_boleta.py` escriben en la base después de enviar (`marcar_enviados_por_xml`) | Un cliente de red no debería persistir. Complica las pruebas y las transacciones. |
| Archivos | XML y PDF en `storage/`, nombrados por tipo y folio, escritos **antes** del `INSERT` | Se sobrescriben cuando un folio se repite (#4). No hay abstracción para pasar a almacenamiento de objetos. |
| Tareas de fondo | RVD y reposición de folios en hilos dentro de uvicorn | Atan la API a un solo proceso. Con dos réplicas se envían dos veces. |
| Caché | Token del SII en memoria **por instancia de `ClienteSII`**, que las rutas crean en cada llamada; resolución del emisor en una tabla | Cada envío o consulta repite semilla → firma → token contra el SII (#13). |
| Autenticación | JWT, API keys sin cuenta, clave efímera de la app y token del MCP | Registro abierto (#1, corregido en la fase 2). Las API keys no ven el keystore. |
| Errores | `core/errors.py` + `api/errors.py`: códigos uniformes y `request-id` | Bien resuelto. Falta mapear `IntegrityError` (#7). |
| Configuración | `pydantic-settings` (`core/config.py`) | Bien. Algunas lecturas directas de `os.environ` (`ADMIN_PASSWORD`, `MCP_AUTH_TOKEN`). |
| Pruebas | 13 scripts `test_*.py` en la raíz, sin pytest; varios dependen del Mac del autor | No hay suite de API ni pruebas de transacción o concurrencia. |
| Dependencias | `requirements.txt` con `>=` | `mcp` 2.x ya rompió `mcp_server.py` (#11). |
| Repositorio | Python mezclado en la raíz (31 `.py`) con Go, Rust y dos apps de escritorio | El fork solo usa Python. El resto es ruido para el equipo. |

**Lo que vale oro y no se toca:** el corazón SII. Son `core/sobre.py`, `dte.py`, `caf.py`,
`crypto.py`, `boleta.py`, `pdf_gen.py`, `schema_validator.py`, `rut.py`, `xml_seguro.py`,
`reloj.py`, `dte_xml.py`, `libro.py`, `reintentos.py` y `errors.py`: unas 3.100 líneas que solo
dependen de la configuración. Sumando `sii.py` y `sii_boleta.py` (877 líneas, un solo import
perezoso de `models`) es todo el conocimiento verificado contra el SII vivo: firma standalone
(L2), orden XSD (L8), hosts asimétricos de boletas, `PROG 1.0` en el User-Agent. El corpus dorado
lo protege byte a byte (15/15 en la imagen, con LF).

## Estándar objetivo

1. **Capas:** rutas (HTTP y validación) → servicios (casos de uso, dueños de la transacción) →
   repositorios (SQL) y adaptadores (SII, archivos, caché). El corazón SII es una biblioteca
   pura que los servicios llaman.
2. **Una transacción por caso de uso.** `EmitirDocumento` reserva el `id_externo` de la venta, consume
   el folio e inserta la fila en la misma transacción. Firma y archivos van después, y su
   resultado se registra en la fila. Ningún adaptador escribe en la base por su cuenta.
3. **Política async:** las rutas que llaman código bloqueante son `def`, para que FastAPI las
   ejecute en su pool de hilos, o usan `run_in_threadpool` explícito. El cliente HTTP del SII
   puede pasar a `httpx.AsyncClient` más adelante, sin tocar la firma.
4. **Datos:** SQLAlchemy 2 Core (`Engine`, pool, dialectos SQLite y Postgres) y **Alembic** para
   todo cambio de esquema. Nada de `CREATE TABLE IF NOT EXISTS` ni `ALTER` condicionales. Cada
   despliegue corre `alembic upgrade head` y cada cambio es una migración revisable con
   `downgrade`.
5. **Inyección de dependencias de FastAPI** (`Depends`): sesión de base, usuario y rol, caché,
   cliente SII. Así las pruebas reemplazan cualquier pieza.
6. **Caché (Redis, opcional por configuración)** solo donde ahorra y no pone en riesgo la
   consistencia:

   | Sí | No |
   |---|---|
   | Token del SII por `(rut, ambiente, servicio)`, TTL menor a la hora que dura | Folios: su verdad es la fila del CAF con candado en la base |
   | Resolución del emisor (hoy en una tabla) | Idempotencia: la verdad está en la base, en la misma transacción que el folio |
   | Límite de intentos de login | Estado del documento ante el SII: se guarda en la fila |
   | Candado de líder para el RVD y la reposición de folios, si hay más de una réplica | |

   Sin Redis configurado, todo funciona igual con un caché en memoria del proceso.
7. **Archivos por adaptador** (`almacen`): nombre por `dte_id` y escritura sin sobrescribir.
   Hoy van al disco local; mañana pueden ir a almacenamiento de objetos sin tocar los servicios.
8. **Tareas de fondo** en un proceso aparte (mismo contenedor y otro comando, o un worker con
   Redis). Mientras sea un solo proceso, con candado de líder.
9. **Pruebas con pytest:** unitarias del corazón (más el corpus dorado), de API con `TestClient`
   sobre SQLite **y** Postgres, y de concurrencia e idempotencia. Los scripts actuales se mueven
   a `tests/scripts/`.
10. **Dependencias fijadas** (`pip-tools` o `uv`) e imagen construida en CI con LF.

## Tres caminos (lo que saldría de una reunión con un senior y el CTO)

| | A. Parchar en el lugar | B. Reescribir desde cero (o desde `E:\www\dte-chile`) | **C. Cascarón nuevo, mismo corazón** |
|---|---|---|---|
| Qué es | Corregir cada fuga sobre la estructura actual | Proyecto FastAPI nuevo con el estándar, reimplementando el dominio | Paquete nuevo con el estándar que **importa el corazón SII sin cambios**; las rutas actuales siguen montadas mientras se reemplazan |
| Riesgo SII | bajo | **alto**: reaprender L2/L8, hosts y User-Agent contra el SII vivo, con folios reales | bajo: el corpus dorado 15/15 es el gate |
| Deuda | crece: cada parche se escribe sobre una forma que no tiene transacciones ni capas | nula al inicio | baja y decreciente |
| Disponible mientras tanto | todo | nada hasta terminar | **todo**: lo que no se migró sigue respondiendo igual |
| Seguir a upstream | fácil | imposible | posible para `core/` (es lo que upstream mantiene como referencia) |

**Sobre `E:\www\dte-chile`:** no es una versión "solo FastAPI" más chica. Es **este mismo motor**
(FastAPI + `core/`) del 2026-07-25, con la misma arquitectura y 16.592 líneas de Python. Desde
esa fecha hubo 23 commits sobre `core/` y `api/`: datos del emisor por negocio, simulación, canal
MIPYME, correcciones de RVD y del registro de envíos. Partir de ahí es perder esas correcciones
sin ganar estructura.

**Recomendación del análisis: C** (no adoptada; se eligió A con el estándar). C es lo que un CTO aprueba: no se arriesga lo que costó plata y días validar
contra el SII, se paga la deuda donde está (aplicación y datos) y no hay "big bang". Además
resuelve el pedido de no cortar nada: las rutas actuales siguen disponibles y solo se mueven al
cascarón cuando su reemplazo pasa las mismas pruebas.

### Forma del cascarón (referencia, no adoptada)

```
faro/                    # paquete nuevo
  app.py                 # create_app(): lifespan, middlewares, routers nuevos + api/routes heredados
  settings.py            # pydantic-settings (compatible con el .env actual)
  api/deps.py            # sesión, usuario, rol, caché, cliente SII
  api/v1/                # emision, documentos, folios, negocio, auth, tokens
  servicios/             # EmitirDocumento, EnviarDocumento, ConsultarEstado… (dueños de la transacción)
  repos/                 # SQL con SQLAlchemy Core: dtes, cafs, emisiones, negocios, usuarios, tokens
  db/                    # engine.py, tablas.py (MetaData), migraciones/ (Alembic)
  adaptadores/           # sii (sobre core/sii*.py, sin escribir en la base), almacen, cache
  tareas/                # RVD y reposición de folios (proceso aparte o candado de líder)
core/                    # corazón SII: sin cambios (corpus dorado)
api/, main.py            # app actual: montada en create_app mientras dura la transición
tests/                   # unit/, api/ (sqlite+postgres), dorado/, scripts/ (los test_*.py de hoy)
```

Los routers heredados usan `core/database.py`. Desde la fase 1 esa fachada corre sobre el mismo
`Engine` que el cascarón, así que ambos ven la misma base y las mismas migraciones.

### Qué pasa con el resto del repositorio

`nucleo/` (Go), `nucleo-rs/` (Rust), `app/`, `app-rs/` y el MCP no se usan en el servidor, pero
**se quedan intactos** (decidido 2026-10-15) para traer cambios del repositorio original.
