# Integrar tu producto con la API de Faro

Cómo llama el backend de tu producto a una instancia de cliente (`http://faro-<cliente>:8000`,
red interna; ver [`README.md`](README.md)). Para el contrato exacto de cada endpoint usa Swagger
en tu instancia local (`http://localhost:8000/docs`). Para los errores, [`../ERRORES-API.md`](../ERRORES-API.md).

> **Estado (2026-10-15, motor Python):** describe la API **actual**. Sirve para aprender y probar
> en local (los mismos llamados están en `http/api/*.http`). **No está lista para integrar
> productos**: tiene fugas verificadas (registro abierto, duplicados al reintentar, folio
> explícito que corrompe documentos) y obliga a enviar emisor y certificado. La API mínima que
> las corrige son las fases 2, 4 y 5 de [`PLAN.md`](PLAN.md); este documento se reescribe al cerrarlas.

## Autenticación

Todas las rutas usan `Authorization: Bearer <token>`. Hoy conviven varios mecanismos porque el
motor también sirve a la app de escritorio (`APP_SHELL_KEY`) y al MCP (`MCP_AUTH_TOKEN`). En el
servidor sobran: el plan (fase 4) deja **un administrador** y **un token de integración por
producto**. Hoy hay dos tipos de token:

| Token | Cómo se obtiene | Ve el keystore | Uso |
|---|---|---|---|
| **JWT** | `POST /api/v1/auth/login` `{"username","password"}`; dura `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` (60) | **Sí**, el del usuario que inició sesión | alta del cliente y, hoy, la integración |
| **API key** `dte_…` | `POST /api/v1/apikeys` `{"nombre": "<producto>"}` con un JWT; se muestra una sola vez | **No**: no tiene cuenta asociada (`usuario["id"]` es `None` en Python, cuenta 0 en Go) | rutas que no dependen del keystore |

El keystore (certificados y negocios) es **por usuario**: lo que sube `admin` solo lo ve
`admin`. Mientras las API keys no tengan cuenta (brecha 1), tu producto inicia sesión con el
usuario dueño del certificado y renueva el JWT al vencer (o ante un 401).

```bash
TOKEN=$(curl -s -X POST http://faro-acme:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<ADMIN_PASSWORD>"}' | jq -r .access_token)
```

## Alta de un cliente (una vez)

Hazla con JWT desde una herramienta interna o un script de onboarding. El orden es el de
[`../PLATAFORMA.md`](../PLATAFORMA.md) §2:

| Paso | Llamada | Notas |
|---|---|---|
| 1. Certificado | `POST /api/v1/keystore/certificados` (multipart: `archivo`, `password`, `nombre`) | Se valida y se guarda **cifrado** (L6). Devuelve `id` = `cert_id`. |
| 2. Negocio | `POST /api/v1/keystore/negocios` (form: `rut`, `cert_id`, `ambiente`) | Completa la razón social desde el SII. Los datos del emisor se corrigen con `PUT /api/v1/keystore/negocios/{id}`. |
| 3. Diagnóstico | `GET /api/v1/onboarding/diagnostico?rut=…&cert_id=…` | Si está autorizado, qué tipos puede emitir y qué falta. |
| 4. Certificación | [`../CERTIFICACION.md`](../CERTIFICACION.md) | Solo si el cliente no está habilitado como emisor con software propio. Tiene pasos humanos. |
| 5. Folios | `POST /api/v1/db/cafs` (subir el CAF) | Después se reponen solos (`FOLIOS_AUTO_*`), con los guardarraíles anti-acaparamiento (L7). |

## Emitir

`POST /api/v1/dte/emitir` recibe un `DTEInput` y enruta por tipo: 39/41 son boletas; 33, 34,
56 y 61 son facturas y notas.

```bash
curl -s -X POST http://faro-acme:8000/api/v1/dte/emitir \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{
    "dte": {
      "tipo_dte": 33,
      "emisor":   {"rut": "77777777-7", "razon_social": "EMPRESA DEMO SPA", "giro": "Cafetería",
                   "codigo_actividad": 561000, "direccion": "Calle Ficticia 123",
                   "comuna": "Santiago", "ciudad": "Santiago"},
      "receptor": {"rut": "76111111-6", "razon_social": "CLIENTE DEMO LTDA", "giro": "Comercio",
                   "direccion": "Av. Siempreviva 742", "comuna": "Santiago"},
      "items": [{"numero_linea": 1, "nombre": "Servicio mensual", "cantidad": 1, "precio_unitario": 10000}]
    }
  }'
```

- `folio: 0` (o sin `folio`): el motor asigna el siguiente de forma atómica (L7).
- `precio_unitario`: **neto** en facturas y notas, **con IVA incluido** en boletas (39/41), como
  las exige el SII (`core/simulacion.py`, `calcular_totales`). El motor calcula los totales.
- **`emisor` es obligatorio hoy** aunque la instancia tenga un solo negocio con todos esos datos
  (tabla `negocios`). En la fase 5 pasa a ser opcional y lo completa el servidor.
- **No reintentes `emitir` a ciegas:** no hay idempotencia y un reintento crea **otro**
  documento con otro folio (hallazgo 3 de [`PLAN.md`](PLAN.md)). **No envíes `folio`
  explícito:** un folio ya usado hace retroceder el correlativo y sobrescribe archivos
  (hallazgo 4).
- Respuesta: `dte_id`, `folio`, `monto_total`, `xml_envio_b64` (sobre firmado), rutas del XML y
  del PDF, `ambiente`.
- **En Python `emitir` no envía al SII.** Genera, firma, timbra y guarda. El envío es otro
  paso (siguiente sección).
- **Certificado:** Python lo toma del cuerpo (`certificado_p12_b64` + `password_certificado`)
  o de `CERTIFICADO_PATH`. No lo toma del keystore (brecha 2).
- **Prueba sin gastar folios:** `POST /api/v1/dte/previsualizar` (`dte` + `cert_id`) firma,
  timbra, valida XSD y devuelve el PDF sin consumir folio ni enviar.

## Enviar y confirmar

| Qué | Python hoy | Go / Rust |
|---|---|---|
| Enviar factura (sobre `EnvioDTE`) | `POST /api/v1/dte/enviar`, con el certificado **en el cuerpo** | además `POST /api/v1/dte/enviar-keystore` (`cert_id`) |
| Enviar boletas (REST del SII) | **sin ruta** (el cliente `core/sii_boleta.py` existe y está verificado en producción, `ACEPTADOS: 1`) | `POST /api/v1/boletas/enviar`, `/boletas/estado` |
| Estado de varios envíos | `POST /api/v1/estado/lote` (`cert_id`, keystore) | igual |
| Estado de un envío | `POST /api/v1/estado/track`, con el certificado en el cuerpo | igual |

**`EPR` no significa aceptado (L3).** Decide con `ACEPTADOS` / `RECHAZADOS` (`todo_aceptado` en
`/estado/lote`). Para el motivo de un rechazo de factura, el código llega solo por correo del SII.

## Consultar lo emitido

- `GET /api/v1/dte/documentos`: listado (filtros en Swagger).
- `GET /api/v1/dte/documentos/{dte_id}/pdf` y `/xml`: el PDF con timbre y el XML firmado.
- `GET /api/v1/monitoreo/folios?rut=…`: folios restantes por tipo.

## Consulta pública de boletas

Sí: además de la página HTML, `/consulta` **es una API**. Es pública (sin token), de solo
lectura y anti-enumeración: exige los datos reales de la boleta.

| Ruta | Devuelve |
|---|---|
| `GET /consulta/api?rut=&tipo=&folio=&monto=&fecha=` | JSON `{"encontrada": false}` o `{"encontrada": true, "documento": {...}, "links": {"pdf": "/consulta/pdf?…", "xml": "/consulta/xml?…"}}` |
| `GET /consulta/pdf?…` / `GET /consulta/xml?…` | el PDF / el XML firmado (mismo candado de 5 datos) |

`tipo` es 39 o 41. `fecha` (`YYYY-MM-DD`) es opcional pero recomendada. Así tu producto puede
servir el buscador con su propia interfaz y Faro no expone nada a Internet:

```
navegador ──► https://tuproducto.ejemplo.cl/boletas/acme   (tu página)
                 └─ backend ──► http://faro-acme:8000/consulta/api?…   (red interna)
                 └─ backend ──► http://faro-acme:8000/consulta/pdf?…   (tu producto entrega el archivo)
```

- Los `links` vienen **relativos a Faro**. Tu backend tiene que pedir el PDF/XML y
  entregarlo él. No los pases tal cual al navegador.
- La URL de tu página es el **Link de Consulta** que se declara al SII en la certificación de
  boletas y que va impresa en la boleta (`../PLATAFORMA.md` §2, paso 4).
- Mantén el candado: no ofrezcas búsquedas con menos datos de los que exige `/consulta/api`.

## Brechas abiertas

Se resuelven en las fases 4 y 5 de [`PLAN.md`](PLAN.md).

Lo que falta en el motor Python para que el producto opere sin tocar el certificado. Cada una
ya existe en Go/Rust o en `core/`. Llevarla a Python **aumenta** la paridad entre motores (P3).
Se mide con `tools/contrato_api.py`.

1. **API keys con cuenta.** Una API key no tiene usuario dueño, así que no ve el keystore. Esto
   pasa en los tres motores. Propuesta: guardar `usuario_id` del creador en `api_keys` y
   resolver la cuenta por ahí. Mientras tanto, JWT.
2. **`cert_id` en `POST /api/v1/dte/emitir`.** Go y Rust lo aceptan (`nucleo/servidor/emitir.go`,
   `nucleo-rs/src/servidor/emitir.rs`). En Python son unas líneas en `api/routes/dte.py` con
   `keystore.cargar_certificado`, como ya hace `/previsualizar`.
3. **`POST /api/v1/dte/enviar-keystore`.** Envío de facturas con `cert_id`. Hay que portarlo de
   `nucleo/servidor/portalrutas.go`.
4. **`POST /api/v1/boletas/enviar` y `/boletas/estado`.** Rutas sobre `core/sii_boleta.py`, con
   el contrato de Go/Rust.
5. **`GET /keystore/negocios/{id}/contexto-emision` en un servidor.** Entrega el certificado y la
   **clave privada en PEM** a cualquier JWT válido. Está pensado para la app de escritorio por
   localhost. En un servidor, la red interna lo mitiga. Lo correcto es desactivarlo fuera de la
   app (por ejemplo, exigir la clave efímera `APP_SHELL_KEY`).

El certificado en el cuerpo de cada petición o un `.pfx` montado con `CERTIFICADO_PATH`
funcionan, pero obligan a tu producto o al servidor a manejar el `.pfx` y su clave. Úsalos
solo para pruebas en certificación.
