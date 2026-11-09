# Integrar tu sistema con la API de Faro

Guía para el backend de una **integración**: el sistema que llama a Faro (punto de venta, tienda
web, ERP). Habla con una instancia de cliente por la red interna (`http://faro-<cliente>:8000`, ver
[`README.md`](README.md)). El contrato exacto de cada ruta está en Swagger de tu instancia local
(`http://localhost:8000/docs`); los errores, en [`../ERRORES-API.md`](../ERRORES-API.md). Los mismos
llamados, listos para ejecutar, están en `http/flujos/` (REST Client de VS Code).

> **Estado (2026-11-09, motor Python, fases 0 a 5 de [`PLAN.md`](PLAN.md)):** describe la API
> actual. Falta el despliegue (fase 6) y probar el despacho contra el SII de certificación con un
> CAF y un certificado reales.

## En una frase

Tu sistema manda el JSON de la venta con su token, recibe **al instante** el folio y el PDF para
entregar, y Faro envía al SII y sigue el veredicto en segundo plano; tu sistema lo consulta
cuando quiera.

```
Tu sistema                        Faro                                   SII
POST /dte/emitir  ─────────────►  timbra, firma, guarda
◄──── 200: dte_id, folio, PDF, estado "generado", envio "programado"
(entregas el PDF al cliente)
                                  segundo plano: envía el sobre ───────►  TrackID
                                  consulta el veredicto ────────────────►  aceptado / rechazado
                                  lo guarda
GET /dte/{dte_id}/estado ──────►
◄──── estado, track_id, motivo_sii
```

**El veredicto nunca viene en la respuesta de emitir.** El SII responde al envío con un TrackID
y decide después: segundos para boletas, de minutos a horas para facturas. Ningún sistema serio
lo espera en la misma llamada.

## Autenticación

Todas las rutas usan `Authorization: Bearer <token>`. Hay dos roles por instancia (una empresa
por despliegue):

| Rol | Credencial | Qué hace |
|---|---|---|
| **administrador** | JWT: `POST /api/v1/auth/login` `{"username","password"}`; dura `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` (60) | todo: usuarios, tokens, auditoría, alertas, alta del cliente |
| **integracion** | token `dte_…`: lo crea el administrador con `POST /api/v1/apikeys` `{"nombre": "punto-de-venta"}` y se muestra **una sola vez** | emitir, enviar, consultar; gestionar certificados, negocios, CAF y folios de la empresa. Recibe 403 en usuarios, tokens, auditoría, alertas y el listado general de documentos |

**Tu sistema usa un token**, no el JWT: no vence y no depende de una contraseña. Uno por
integración, así la auditoría (`GET /api/v1/auditoria`, administrador) dice cuál hizo qué.

- **Ve solo lo suyo.** Un documento que no emitió ese token responde 404.
- **Renovar** (rutina): `POST /api/v1/apikeys/propia/renovar` con el token, o
  `POST /api/v1/apikeys/{id}/renovar` el administrador. Devuelve la clave nueva; **la anterior
  muere en el acto**: guarda la nueva antes de cambiarla en tu configuración. El `id` no cambia:
  tus `id_externo` siguen valiendo.
- **Revocar** (clave filtrada): `DELETE /api/v1/apikeys/{id}` (administrador). Corta al instante.
- **Límites:** 5 logins fallidos por usuario+IP bloquean 15 minutos; 20 Bearer inválidos por IP
  hacen que los inválidos respondan 429. Respeta `Retry-After`.

```bash
TOKEN=dte_...   # el que te entregó el administrador; guárdalo como secreto de tu sistema
curl -s http://faro-acme:8000/api/v1/auth/me -H "Authorization: Bearer $TOKEN"
# {"tipo": "apikey", "id": 3, "nombre": "punto-de-venta", "rol": "integracion"}
```

## Alta de un cliente (una vez)

La hace el administrador (o una herramienta interna con su JWT). El orden es el de
[`../PLATAFORMA.md`](../PLATAFORMA.md) §2:

| Paso | Llamada | Notas |
|---|---|---|
| 1. Certificado | `POST /api/v1/keystore/certificados` (multipart: `archivo`, `password`, `nombre`) | Se valida y se guarda **cifrado** (L6). Devuelve `id` = `cert_id`. Tu sistema nunca vuelve a manejar el `.pfx`. |
| 2. Negocio | `POST /api/v1/keystore/negocios` (form: `rut`, `cert_id`, `ambiente`) | Completa la razón social desde el SII. |
| 3. Datos del emisor | `PUT /api/v1/keystore/negocios/{id}` (`giro`, `codigo_actividad`, `direccion`, `comuna`, `ciudad`) | Con ellos, emitir no necesita `emisor`. |
| 4. Diagnóstico | `GET /api/v1/onboarding/diagnostico?rut=…&cert_id=…` | Si está autorizado, qué tipos puede emitir y qué falta. |
| 5. Certificación | [`../CERTIFICACION.md`](../CERTIFICACION.md) | Solo si el cliente no está habilitado como emisor con software propio. Tiene pasos humanos. |
| 6. Folios | `POST /api/v1/caf` (subir el CAF) | Después se reponen solos (`FOLIOS_AUTO_*`), con los guardarraíles anti-acaparamiento (L7). |
| 7. Token | `POST /api/v1/apikeys` | Uno por integración. |

## Emitir

```bash
curl -s -X POST http://faro-acme:8000/api/v1/dte/emitir \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{
    "id_externo": "pedido-1042",
    "dte": {
      "tipo_dte": 33,
      "receptor": {"rut": "76111111-6", "razon_social": "CLIENTE DEMO LTDA", "giro": "Comercio",
                   "direccion": "Av. Siempreviva 742", "comuna": "Santiago"},
      "items": [{"numero_linea": 1, "nombre": "Servicio mensual", "cantidad": 1, "precio_unitario": 10000}]
    }
  }'
```

Respuesta (200), al instante:

```json
{"dte_id": 81, "folio": 1042, "tipo_dte": 33, "monto_total": 11900, "estado": "generado",
 "envio": "programado", "id_externo": "pedido-1042", "repetido": false,
 "xml_envio_b64": "…", "pdf_path": "…", "ambiente": "certificacion", "mensaje": "DTE emitido. Folio 1042."}
```

- **El PDF ya vale para entregar**: está timbrado (`GET /api/v1/dte/documentos/{dte_id}/pdf`).
- **`emisor` es opcional**: sin él, Faro usa los datos del negocio (paso 3 del alta). Con varios
  negocios en la instancia, manda `rut_emisor`. Si le faltan datos al negocio, 422 con la lista.
- **Manda siempre `id_externo`** (el identificador de la venta en tu sistema). Si no recibiste la
  respuesta, reintenta con el mismo JSON y el mismo `id_externo`: devuelve el mismo documento
  (`repetido: true`), sin gastar otro folio. Un JSON distinto sobre un documento ya timbrado
  responde 409 `ya_emitido` (se corrige con una nota de crédito o débito). Reglas completas en
  [`PLAN.md`](PLAN.md#política-de-folios) (timbrado = emitido).
- `folio: 0` (o sin `folio`): Faro asigna el siguiente de forma atómica (L7). Elegir el folio es
  solo del administrador.
- `precio_unitario`: **neto** en facturas y notas, **con IVA incluido** en boletas (39/41). Faro
  calcula los totales.
- Los datos inválidos responden 422 **sin gastar folio** (RUT, referencias de 56/61, esquema XSD).
- **`envio`** dice qué pasa con el envío: `programado` (Faro lo envía), `no_solicitado` (mandaste
  `"enviar": false`), `desactivado` (la instancia tiene `ENVIO_SII=desactivado`) o, en un
  reintento, el estado actual.
- **Probar sin gastar folios:** `POST /api/v1/dte/previsualizar` (`dte`; `cert_id` opcional)
  firma, timbra, valida XSD y devuelve el PDF marcado **BORRADOR**, sin consumir folio ni enviar.

## Seguir el veredicto

`GET /api/v1/dte/{dte_id}/estado`:

```json
{"dte_id": 81, "folio": 1042, "estado": "aceptado", "track_id": "4242", "motivo_sii": null,
 "enviado_en": "…", "veredicto_en": "…", "seguimiento": true, "intentos_envio": 0, "envio_sii": "activo"}
```

| `estado` | Qué significa | Qué hace tu sistema |
|---|---|---|
| `generado` | Timbrado y guardado; todavía no sale al SII. | Nada: el PDF ya se entrega. |
| `enviado` | El SII lo recibió (hay `track_id`); falta el veredicto. | Esperar. |
| `aceptado` | El SII lo aceptó. | Nada. |
| `aceptado_con_reparos` | Aceptado, con observaciones (`motivo_sii`). | Revisar; no se reemite. |
| `rechazado` | El SII lo rechazó (`motivo_sii`). **El folio quedó quemado**: no se reenvía. | Emitir uno nuevo con **otro** `id_externo`. |
| `envio_fallido` | Se agotaron los reintentos de envío (`motivo_sii`). | Avisar; reintentar con `POST /dte/{dte_id}/enviar`. |
| `sin_veredicto` | Pasaron `VEREDICTO_ALERTA_HORAS` (6) sin respuesta del SII. Faro sigue preguntando. | Esperar o revisar en el portal del SII. |

- Faro actualiza el estado solo. Consulta cada pocos minutos, o con `?consultar_sii=true` para
  que pregunte al SII en ese momento (como mucho cada 30 segundos).
- **`EPR` no es aceptado** (L3): el veredicto sale del desglose por documento.
- El código del rechazo de una **boleta** viene en `motivo_sii`. El de una **factura** el SII lo
  manda **solo por correo**: `motivo_sii` lo avisa.
- El administrador ve lo que necesita atención en `GET /api/v1/dte/alertas`: `sin_veredicto`,
  `envio_fallido` y `folios_por_anular` (los rechazados).

## Si tu sistema quiere manejar el envío

Manda `"enviar": false` al emitir y envía tú:

| Paso | Llamada | Respuesta |
|---|---|---|
| 1 | `POST /api/v1/dte/emitir` con `"enviar": false` | folio y PDF; `envio: "no_solicitado"` |
| 2 | `POST /api/v1/dte/{dte_id}/enviar` | estado `enviado` con `track_id`. Un documento ya enviado no se reenvía: responde su estado. 502 si el SII no responde. |
| 3 | `GET /api/v1/dte/{dte_id}/estado` | el veredicto, cuando llegue (Faro lo sigue igual después del paso 2) |

Es lo mismo que hace el despacho, con más trabajo para tu sistema: reintentar si el SII no
responde y vigilar que nada quede sin veredicto. Recomendado solo si necesitas controlar cuándo
sale cada documento.

## Pruebas y producción: las mismas llamadas

Tu sistema no cambia nada entre ambientes. Cambia la instancia:

| Ambiente | Configuración de la instancia | Qué pasa |
|---|---|---|
| Local | `ENVIO_SII=desactivado` (por defecto), CAF y certificado ficticios del corpus | Emite y entrega el PDF. `envio: "desactivado"`, el estado queda `generado`, nada sale al SII. Para desarrollar la integración. |
| Certificación | `ENVIO_SII=activo`, `SII_AMBIENTE=certificacion`, CAF y certificado reales | Envía al SII de pruebas (facturas a maullin, boletas a pangal). Veredicto real. |
| Producción | `ENVIO_SII=activo`, `SII_AMBIENTE=produccion` | Envía al SII real (palena y rahue). |

Mismo JSON, mismas respuestas, mismos estados. **Por verificar en certificación:** si la factura
debe estar aceptada por el SII antes de entregarla al receptor (la boleta se entrega al tiro).

## Consultar lo emitido

- `GET /api/v1/dte/documentos/{dte_id}`: un documento (estado, montos, TrackID). Con un token,
  solo los que emitió.
- `GET /api/v1/dte/documentos/buscar?rut_emisor=&tipo_dte=&folio=`: el mismo, por folio.
- `GET /api/v1/dte/documentos/{dte_id}/pdf` y `/xml`: el PDF con timbre y el XML firmado.
- `GET /api/v1/dte/documentos`: listado con filtros (administrador).
- `GET /api/v1/caf/folios/siguiente/{rut}/{tipo}` y `GET /api/v1/monitoreo/folios?rut=…`: folios
  restantes.

## Consulta pública de boletas

Además de la página HTML, `/consulta` **es una API**. Es pública (sin token), de solo lectura y
anti-enumeración: exige los datos reales de la boleta.

| Ruta | Devuelve |
|---|---|
| `GET /consulta/api?rut=&tipo=&folio=&monto=&fecha=` | JSON `{"encontrada": false}` o `{"encontrada": true, "documento": {...}, "links": {"pdf": "/consulta/pdf?…", "xml": "/consulta/xml?…"}}` |
| `GET /consulta/pdf?…` / `GET /consulta/xml?…` | el PDF / el XML firmado (mismo candado de 5 datos) |

`tipo` es 39 o 41. `fecha` (`YYYY-MM-DD`) es opcional pero recomendada. Así tu integración puede
servir el buscador con su propia interfaz y Faro no expone nada a Internet:

```
navegador ──► https://tusistema.ejemplo.cl/boletas/acme   (tu página)
                 └─ backend ──► http://faro-acme:8000/consulta/api?…   (red interna)
                 └─ backend ──► http://faro-acme:8000/consulta/pdf?…   (tu sistema entrega el archivo)
```

- Los `links` vienen **relativos a Faro**. Tu backend pide el PDF/XML y lo entrega él. No los
  pases tal cual al navegador.
- La URL de tu página es el **Link de Consulta** que se declara al SII en la certificación de
  boletas y que va impresa en la boleta (`../PLATAFORMA.md` §2, paso 4).
- Mantén el candado: no ofrezcas búsquedas con menos datos de los que exige `/consulta/api`.

## Rutas heredadas

Siguen por compatibilidad (la app de escritorio, los motores Go y Rust, `tools/contrato_api.py`).
Una integración nueva no las necesita:

- `POST /api/v1/dte/enviar` (sobre y certificado **en el cuerpo**), `POST /api/v1/estado/track` y
  `/estado/lote`: envío y consulta por TrackID sin pasar por el documento.
- `certificado_p12_b64` + `password_certificado` en emitir: el despacho no los conoce; un documento
  emitido así se envía con el certificado del negocio.
- `/api/v1/db/*`: alias obsoleto, solo administrador.
