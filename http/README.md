# Llamadas a la API desde el editor (REST Client)

Alternativa a Swagger para probar la API de Faro sin salir de VS Code. Requiere la extensión
**REST Client** (`humao.rest-client`); VS Code la sugiere al abrir el repo (`.vscode/extensions.json`).

## Preparar

1. Levanta una instancia: la local (`docker compose up -d`, ver
   [`../docs/deploy/README.md`](../docs/deploy/README.md)) o la de tu servidor.
2. Copia `env.ejemplo` como `http/.env` (ignorado por git): `BASE_URL` (la dirección de la
   instancia) y `FARO_CLAVE` (su `ADMIN_PASSWORD`). Cambiar de instancia es cambiar esas dos líneas.
3. Ejecuta los bloques en orden con "Send Request". El login de cada archivo deja el token para el
   resto del archivo (dura 60 minutos).

## Qué hay

| Carpeta | Contenido |
|---|---|
| `api/` | **Todas** las operaciones de `/openapi.json` (92, en 16 archivos, uno por grupo). Las genera `tools/generar_http.py`: no se editan a mano. Los cuerpos traen solo los campos obligatorios con datos ficticios; los opcionales se listan en un comentario `# opcionales:`. |
| `flujos/` | Recorridos escritos a mano, **en el orden de una instancia nueva**. Cada paso dice qué respuesta esperar. |

| Flujo | Quién | Qué hace |
|---|---|---|
| `01-configurar.http` | administrador, una vez | empresa → certificado → CAF (33 y 39) → revisar → crear el token de tu sistema (cópialo a `FARO_TOKEN`) |
| `02-emitir.http` | tu sistema (token) | factura con `id_externo`, reintento `repetido`, `409 ya_emitido`, `422` sin gastar folio, boleta, estado, PDF, envío a mano, BORRADOR, `403` fuera de su rol |
| `03-administrar.http` | administrador | documentos, alertas, tokens (crear, renovar, revocar), auditoría, registro cerrado y bloqueo de login |
| `04-ambientes.http` | administrador | ambiente de la empresa, CAF de producción antes del cambio, requisitos para salir de demo, empresa única |

Regenerar `api/` cuando cambie una ruta (con la instancia local arriba):

```bash
python tools/generar_http.py
```

## Cuidado

- Algunas operaciones **gastan folios o hablan con el SII** (`dte/emitir`, `dte/enviar`,
  `estado/*`, `onboarding/*`, `keystore/negocios/*/timbraje`, `mipyme/*`). En una instancia con
  certificado y CAF reales, el ambiente de la empresa (`GET /api/v1/empresa`) decide si va a
  certificación o a producción; en modo demo (`simulacion`) nada sale al SII (L1 de
  [`../docs/CONSTITUCION.md`](../docs/CONSTITUCION.md)).
- Los archivos de ejemplo para subir (`< ../../tests/dorado/fixtures/...`) son el CAF y el
  certificado **ficticios** del corpus dorado. Sirven para practicar; el SII no los acepta.
- No pongas claves en los `.http`: van en `http/.env`.
