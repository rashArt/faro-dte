# Integrar tu producto con la API de Faro

Cómo llama el backend de tu producto a una instancia de cliente (`http://faro-<cliente>:8000`,
red interna; ver [`README.md`](README.md)). Para el contrato exacto de cada endpoint usa Swagger
en tu instancia local (`http://localhost:8000/docs`). Para los errores, [`../ERRORES-API.md`](../ERRORES-API.md).

> **Estado (2026-10-30, motor Python):** describe la API **actual**. Sirve para aprender y probar
> en local (los mismos llamados están en `http/api/*.http` y `http/flujos/`). Las fases 2 y 4 de
> [`PLAN.md`](PLAN.md) cerraron las fugas de integridad y dejaron roles y tokens por sistema.
> **Todavía falta** enviar y consultar por documento y el emisor opcional (fase 5); este
> documento se reescribe al cerrarla.

## Autenticación

Todas las rutas usan `Authorization: Bearer <token>`. Hay dos roles por instancia (una empresa
por despliegue):

| Rol | Credencial | Qué hace |
|---|---|---|
| **administrador** | JWT: `POST /api/v1/auth/login` `{"username","password"}`; dura `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` (60) | todo: usuarios, tokens, auditoría, alta del cliente |
| **integracion** | token `dte_…` de tu sistema: lo crea el administrador con `POST /api/v1/apikeys` `{"nombre": "punto-de-venta"}` y se muestra **una sola vez** | emitir, enviar, consultar; gestionar certificados, negocios, CAF y folios de la empresa. Recibe 403 en usuarios, tokens, auditoría y el listado general de documentos |

**Tu producto usa un token**, no el JWT: no vence y no depende de una contraseña. Uno por
sistema que llama (punto de venta, tienda, ERP), así la auditoría (`GET /api/v1/auditoria`,
administrador) dice cuál hizo qué.

- **Ve solo lo suyo.** Un documento que no emitió ese token responde 404.
- **Renovar** (rutina): `POST /api/v1/apikeys/propia/renovar` con el token, o
  `POST /api/v1/apikeys/{id}/renovar` el administrador. Devuelve la clave nueva; **la anterior
  muere en el acto**, así que guarda la nueva antes de cortar la vieja en tu configuración. El
  `id` no cambia: tus `id_externo` siguen valiendo.
- **Revocar** (clave filtrada): `DELETE /api/v1/apikeys/{id}` (administrador). Corta al instante.
- **Límites:** 5 logins fallidos por usuario+IP bloquean 15 minutos; 20 Bearer inválidos por IP
  hacen que los inválidos respondan 429. Respeta `Retry-After`.

```bash
TOKEN=dte_...   # el que te entregó el administrador; guárdalo como secreto de tu sistema
curl -s http://faro-acme:8000/api/v1/auth/me -H "Authorization: Bearer $TOKEN"
# {"tipo": "apikey", "id": 3, "nombre": "punto-de-venta", "rol": "integracion"}
```

## Alta de un cliente (una vez)

Hazla con el JWT del administrador desde una herramienta interna o un script de onboarding
(el token de integración también puede subir certificados y CAF). El orden es el de
[`../PLATAFORMA.md`](../PLATAFORMA.md) §2:

| Paso | Llamada | Notas |
|---|---|---|
| 1. Certificado | `POST /api/v1/keystore/certificados` (multipart: `archivo`, `password`, `nombre`) | Se valida y se guarda **cifrado** (L6). Devuelve `id` = `cert_id`. |
| 2. Negocio | `POST /api/v1/keystore/negocios` (form: `rut`, `cert_id`, `ambiente`) | Completa la razón social desde el SII. Los datos del emisor se corrigen con `PUT /api/v1/keystore/negocios/{id}`. |
| 3. Diagnóstico | `GET /api/v1/onboarding/diagnostico?rut=…&cert_id=…` | Si está autorizado, qué tipos puede emitir y qué falta. |
| 4. Certificación | [`../CERTIFICACION.md`](../CERTIFICACION.md) | Solo si el cliente no está habilitado como emisor con software propio. Tiene pasos humanos. |
| 5. Folios | `POST /api/v1/caf` (subir el CAF) | Después se reponen solos (`FOLIOS_AUTO_*`), con los guardarraíles anti-acaparamiento (L7). |

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
- **Manda `id_externo`** (el identificador de la venta en tu producto) para reintentar sin
  duplicar: el mismo JSON con el mismo `id_externo` devuelve el documento ya emitido
  (`repetido: true`); un JSON distinto con un documento ya timbrado responde 409 `ya_emitido`.
  Sin `id_externo`, cada petición es un documento nuevo. Reglas completas en
  [`PLAN.md`](PLAN.md#política-de-folios) (timbrado = emitido).
- **`folio` explícito:** solo el administrador, y nunca uno ya usado (409, el correlativo no
  retrocede).
- Respuesta: `dte_id`, `folio`, `estado` (`generado`), `id_externo`, `repetido`, `monto_total`,
  `xml_envio_b64` (sobre firmado), rutas del XML y del PDF, `ambiente`. Los datos inválidos
  responden 422 **sin gastar folio** (RUT, referencias de 56/61, esquema XSD).
- **En Python `emitir` no envía al SII.** Genera, firma, timbra y guarda. El envío es otro
  paso (siguiente sección).
- **Certificado:** el del negocio del emisor en el keystore (`negocios.cert_id`). El del cuerpo
  (`certificado_p12_b64` + `password_certificado`) sigue por compatibilidad, y sin ninguno de
  los dos, `CERTIFICADO_PATH`.
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

- `GET /api/v1/dte/documentos/{dte_id}`: un documento (estado, montos, TrackID). Con un token,
  solo los que emitió.
- `GET /api/v1/dte/documentos/buscar?rut_emisor=&tipo_dte=&folio=`: el mismo, por folio.
- `GET /api/v1/dte/documentos/{dte_id}/pdf` y `/xml`: el PDF con timbre y el XML firmado.
- `GET /api/v1/dte/documentos`: listado con filtros (administrador).
- `GET /api/v1/caf/folios/siguiente/{rut}/{tipo}`: siguiente folio del CAF activo.
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

1. ~~**API keys con cuenta.**~~ Resuelto en la fase 4 (solo Python): el token opera sobre la
   cuenta dueña de la instancia, con rol `integracion`.
2. ~~**`cert_id` en `POST /api/v1/dte/emitir`.**~~ Resuelto en la fase 2 de otra forma: Python
   toma el certificado del negocio del emisor, sin `cert_id` en el cuerpo.
3. **`POST /api/v1/dte/enviar-keystore`.** Envío de facturas con `cert_id`. Hay que portarlo de
   `nucleo/servidor/portalrutas.go`.
4. **`POST /api/v1/boletas/enviar` y `/boletas/estado`.** Rutas sobre `core/sii_boleta.py`, con
   el contrato de Go/Rust.
5. ~~**`GET /keystore/negocios/{id}/contexto-emision` en un servidor.**~~ Resuelto en la fase 2:
   esa ruta y `mipyme/contexto-siiengine` exigen la clave efímera de la app (`APP_SHELL_KEY`);
   un JWT o una API key reciben 403.

El certificado en el cuerpo de cada petición o un `.pfx` montado con `CERTIFICADO_PATH`
funcionan, pero obligan a tu producto o al servidor a manejar el `.pfx` y su clave. Úsalos
solo para pruebas en certificación.
