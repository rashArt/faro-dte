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
| **administrador** | JWT: `POST /api/v1/auth/login` `{"username","password"}`; dura `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` (60) | todo: alta del cliente (datos de la empresa, certificado, ambiente), tokens, auditoría, alertas |
| **integracion** | token `dte_…`: lo crea el administrador con `POST /api/v1/apikeys` `{"nombre": "punto-de-venta"}` y se muestra **una sola vez** | emitir, enviar, consultar; ver la empresa y sus folios; cargar CAF. Recibe 403 al cambiar los datos de la empresa, su certificado o su ambiente, al fijar el CAF en uso, y en usuarios, tokens, auditoría, alertas y el listado general de documentos |

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

La hace el administrador (o una herramienta interna con su JWT): los pasos 1, 2, 5 y 6 responden
403 a un token de integración. Una instancia atiende a **una sola empresa**, así que las rutas no
llevan id ni RUT (fase 7 de [`PLAN.md`](PLAN.md)):

| Paso | Llamada | Notas |
|---|---|---|
| 1. Empresa | `PUT /api/v1/empresa` (`rut`, `razon_social`, `giro`, `codigo_actividad`, `direccion`, `comuna`, `ciudad`) | Nace en **modo demo** (`simulacion`): no se consulta al SII, basta un RUT bien formado. Otro RUT responde 409 `empresa_unica`. |
| 2. Certificado | `PUT /api/v1/empresa/certificado` (multipart: `archivo`, `password`, `nombre`) | Se valida y se guarda **cifrado** (L6). Tu sistema nunca vuelve a manejar el `.pfx`. |
| 3. Folios | `POST /api/v1/caf` (el CAF del ambiente actual; `ambiente` para cargar el de otro) | Fuera de demo se reponen solos (`FOLIOS_AUTO_*`), con los guardarraíles anti-acaparamiento (L7). |
| 4. Token | `POST /api/v1/apikeys` | Uno por integración. |
| 5. Certificación | `PUT /api/v1/empresa/ambiente` con `"ambiente": "certificacion"`; después [`../CERTIFICACION.md`](../CERTIFICACION.md) | **Aquí se valida el RUT en el SII**: si no lo conoce, 409 `requisitos_certificacion`. La certificación del SII tiene pasos humanos y solo hace falta si el cliente no está habilitado como emisor con software propio. |
| 6. Producción | `POST /api/v1/caf` con `ambiente=produccion` y `PUT /api/v1/empresa/ambiente` | Ver [Pasar a producción](#pasar-a-producción). |

`GET /api/v1/empresa` muestra los datos, el ambiente, el certificado y lo que falta para pasar a
producción. En demo nada sale al SII ni se le consulta nada (ni el RUT, ni la resolución, ni
folios): sirve para aprender la API con datos ficticios. Quien instala puede hacer que la empresa
nazca directo en certificación con `SII_AMBIENTE=certificacion`; entonces el RUT se valida al
crearla. Las rutas antiguas `/api/v1/keystore/negocios/*` siguen existiendo para la interfaz
heredada; no las uses en una integración nueva.

### Pasar a producción

Certificación y producción son ambientes separados del SII: cada uno entrega sus CAF y numera
por su cuenta. En Faro el ambiente es **de la empresa** y se cambia en la misma instancia:

```bash
# 1. El CAF de producción se carga antes, sin afectar lo que se emite hoy.
curl -s -X POST http://faro-acme:8000/api/v1/caf -H "Authorization: Bearer $ADMIN" \
  -F archivo=@CAF_33_produccion.xml -F ambiente=produccion
# 2. El cambio. `confirmar_rut` repite el RUT de la empresa.
curl -s -X PUT http://faro-acme:8000/api/v1/empresa/ambiente -H "Authorization: Bearer $ADMIN" \
  -H 'Content-Type: application/json' -d '{"ambiente": "produccion", "confirmar_rut": "76111111-6"}'
```

- Se rechaza con 409 `requisitos_produccion` (y la lista en `detalle.falta`) si faltan datos de
  la empresa, un certificado vigente, un CAF de producción vigente, secretos seguros en el
  `.env`, o si el SII no muestra a la empresa autorizada en producción.
- Cada CAF, documento, `id_externo` y RVD guarda el ambiente en que nació: el folio 1 y el
  `pedido-1` de las pruebas no chocan con los reales. Un documento de certificación pendiente
  se sigue consultando en el SII de pruebas.
- Volver a certificación o a demo es la misma llamada y no pide requisitos.
- El cambio queda en la auditoría (`cambio_ambiente`, con quién y de qué ambiente a cuál).

### Varios CAF y vencimiento

Se puede cargar un CAF nuevo antes de que se acabe el anterior: no lo apaga. Los folios salen
del CAF **en uso** (el vigente que vence primero) y, cuando se agota o vence, Faro pasa solo al
siguiente. Un CAF vence a los **6 meses** de su autorización (Res. Ex. SII 58/2017; Faro lo
aplica a todos los tipos): no se usa nunca, porque el SII rechazaría el documento (`CAF-3-517`)
y el folio se perdería. `GET /api/v1/empresa/folios` muestra lo disponible y cuándo vence cada
CAF; el administrador puede fijar otro con `PUT /api/v1/caf/{id}/en-uso`.

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
- **`emisor` es opcional**: sin él, Faro usa los datos de la empresa (paso 1 del alta). Si le
  faltan datos, 422 con la lista.
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
  `"enviar": false`), `desactivado` (la instancia tiene `ENVIO_SII=desactivado`), `simulacion`
  (la empresa está en modo demo: el documento nunca se envía) o, en un
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

Tu sistema no cambia nada entre ambientes. Cambia el ambiente de la empresa (por la API) y si
la instancia envía al SII (`ENVIO_SII` del `.env`):

| Ambiente | Cómo se llega | Qué pasa |
|---|---|---|
| Demo (`simulacion`) | La empresa nace así; CAF y certificado ficticios del corpus | Emite y entrega el PDF. `envio: "simulacion"`, el estado queda `generado`, nada sale al SII aunque `ENVIO_SII` esté activo. Para aprender y desarrollar la integración. |
| Certificación | `ENVIO_SII=activo`; `PUT /api/v1/empresa/ambiente` (valida el RUT); CAF de certificación y certificado real | Envía al SII de pruebas (facturas a maullin, boletas a pangal). Veredicto real. |
| Producción | `ENVIO_SII=activo`; `PUT /api/v1/empresa/ambiente` (exige los requisitos); CAF de producción | Envía al SII real (palena y rahue). |

Mismo JSON, mismas respuestas, mismos estados. **Por verificar en certificación:** si la factura
debe estar aceptada por el SII antes de entregarla al receptor (la boleta se entrega al tiro).

## Consultar lo emitido

- `GET /api/v1/dte/documentos/{dte_id}`: un documento (estado, montos, TrackID). Con un token,
  solo los que emitió.
- `GET /api/v1/dte/documentos/buscar?rut_emisor=&tipo_dte=&folio=`: el mismo, por folio.
- `GET /api/v1/dte/documentos/{dte_id}/pdf` y `/xml`: el PDF con timbre y el XML firmado.
- `GET /api/v1/dte/documentos`: listado con filtros (administrador).
- `GET /api/v1/empresa/folios`: folios restantes por tipo y vencimiento de cada CAF.

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
