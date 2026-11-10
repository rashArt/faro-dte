# Previsualizar sin gastar folios

`POST /api/v1/dte/previsualizar` arma el documento completo: lo timbra, lo firma, lo valida contra
el esquema del SII y genera el PDF. **No gasta un folio, no guarda nada y no envía nada.** El PDF
sale marcado como **BORRADOR**.

Sirve para:

- mostrar al usuario cómo quedará el documento antes de confirmarlo;
- probar una integración nueva contra una instancia real sin consumir folios;
- comprobar que la empresa está bien configurada (certificado, datos del emisor y CAF).

## Petición

El cuerpo lleva el mismo `dte` que una emisión, sin `id_externo` ni `enviar`:

```bash
curl -s -X POST "$FARO_URL/api/v1/dte/previsualizar" \
  -H "Authorization: Bearer $FARO_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "dte": {
      "tipo_dte": 33,
      "receptor": {"rut": "76111111-6", "razon_social": "CLIENTE DEMO LTDA",
                   "giro": "Comercio", "direccion": "Av. Siempreviva 742", "comuna": "Santiago"},
      "items": [{"numero_linea": 1, "nombre": "Servicio mensual", "cantidad": 1, "precio_unitario": 10000}]
    }
  }'
```

## Respuesta

```json
{
  "es_preview": true,
  "aviso": "Previsualización — NO enviada al SII, no consume folios.",
  "tipo_dte": 33,
  "monto_neto": 10000,
  "monto_exento": 0,
  "iva": 1900,
  "monto_total": 11900,
  "valido_xsd": true,
  "errores_xsd": [],
  "xml_b64": "PD94bWwg...",
  "pdf_b64": "JVBERi0x..."
}
```

| Campo | Qué es |
|---|---|
| `monto_neto`, `monto_exento`, `iva`, `monto_total` | Los totales que tendrá el documento real |
| `valido_xsd`, `errores_xsd` | Si el documento cumple el esquema del SII, y por qué no |
| `pdf_b64` | El PDF con la marca BORRADOR, en base64 |
| `xml_b64` | El XML firmado de muestra, en base64 |

Para guardar el PDF:

```bash
curl -s ... | python -c "import sys,json,base64; open('borrador.pdf','wb').write(base64.b64decode(json.load(sys.stdin)['pdf_b64']))"
```

!!! danger "El borrador no se entrega"
    El PDF de la previsualización no tiene validez tributaria: el folio es de muestra y el
    documento no existe para el SII. Para un documento real, usa
    [Emitir](emitir.md).

## Errores

Los mismos de una emisión, salvo los de folios e idempotencia. Además, si la empresa no tiene un
CAF cargado para ese tipo, responde `409` con `codigo: "folio"`: aunque no gaste folios, la
previsualización necesita el CAF para el timbre.
