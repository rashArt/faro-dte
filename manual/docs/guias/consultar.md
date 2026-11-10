# Consultar lo emitido

Un token de integración puede consultar y descargar **los documentos que emitió**. Si pide uno
ajeno, recibe `404`.

| Para | Ruta |
|---|---|
| Ver un documento | `GET /api/v1/dte/documentos/{dte_id}` |
| Buscarlo por folio | `GET /api/v1/dte/documentos/buscar?rut_emisor=…&tipo_dte=…&folio=…` |
| Descargar el PDF | `GET /api/v1/dte/documentos/{dte_id}/pdf` |
| Descargar el XML firmado | `GET /api/v1/dte/documentos/{dte_id}/xml` |
| Ver el estado ante el SII | `GET /api/v1/dte/{dte_id}/estado` (ver [Seguir el veredicto](seguir-veredicto.md)) |
| Folios restantes | `GET /api/v1/caf/folios/siguiente/{rut}/{tipo}` |

## Un documento

```bash
curl -s "$FARO_URL/api/v1/dte/documentos/81" -H "Authorization: Bearer $FARO_TOKEN"
```

```json
{
  "id": 81,
  "tipo_dte": 33,
  "folio": 1042,
  "rut_emisor": "77777777-7",
  "rut_receptor": "76111111-6",
  "razon_social_receptor": "CLIENTE DEMO LTDA",
  "fecha_emision": "2026-11-09",
  "monto_neto": 10000,
  "monto_exento": 0,
  "iva": 1900,
  "monto_total": 11900,
  "estado": "aceptado",
  "track_id": 4242,
  "ambiente": "produccion",
  "creado_en": "2026-11-09T10:15:00.912000"
}
```

## Buscar por folio

Útil cuando el cliente final trae el número impreso en el documento:

```bash
curl -s "$FARO_URL/api/v1/dte/documentos/buscar?rut_emisor=77777777-7&tipo_dte=33&folio=1042" \
  -H "Authorization: Bearer $FARO_TOKEN"
```

## Descargar PDF y XML

```bash
curl -s "$FARO_URL/api/v1/dte/documentos/81/pdf" -H "Authorization: Bearer $FARO_TOKEN" -o factura-1042.pdf
curl -s "$FARO_URL/api/v1/dte/documentos/81/xml" -H "Authorization: Bearer $FARO_TOKEN" -o factura-1042.xml
```

- El **PDF** es la representación impresa, con el timbre electrónico. Es lo que se entrega al
  cliente.
- El **XML** es el documento tributario firmado, en codificación ISO-8859-1. Algunas empresas
  receptoras lo piden para su contabilidad. Guárdalo tal cual: cualquier cambio invalida la firma.

!!! tip "Guarda tus propias copias"
    Faro conserva los documentos, pero es buena práctica que tu sistema archive el PDF y el XML de
    cada venta.

## Folios restantes

```bash
curl -s "$FARO_URL/api/v1/caf/folios/siguiente/77777777-7/33" -H "Authorization: Bearer $FARO_TOKEN"
```

```json
{"rut_emisor": "77777777-7", "tipo_dte": 33, "folio_disponible": 1043,
 "folio_desde": 1001, "folio_hasta": 1100, "mensaje": "..."}
```

Quedan `folio_hasta - folio_disponible + 1` folios en el CAF activo (58 en el ejemplo). Cuando se
acaban, emitir responde `409 sin_folios` hasta que se cargue un CAF nuevo.
