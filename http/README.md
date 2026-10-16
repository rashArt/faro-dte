# Llamadas a la API desde el editor (REST Client)

Alternativa a Swagger para probar la API de Faro sin salir de VS Code. Requiere la extensión
**REST Client** (`humao.rest-client`); VS Code la sugiere al abrir el repo (`.vscode/extensions.json`).

## Preparar

1. Levanta la instancia local (`docker compose up -d`, ver [`../docs/deploy/README.md`](../docs/deploy/README.md)).
2. Copia `env.ejemplo` como `http/.env` (ignorado por git) y pon tu `ADMIN_PASSWORD` en `FARO_CLAVE`.
3. Elige el entorno **local** (Ctrl+Alt+E). Está definido en `.vscode/settings.json` (`base`, `usuario`).
4. En cada archivo, ejecuta primero **Iniciar sesión** ("Send Request" sobre el bloque). El
   token queda en `{{token}}` para el resto del archivo y dura 60 minutos.

## Qué hay

| Carpeta | Contenido |
|---|---|
| `api/` | **Todas** las operaciones de `/openapi.json` (80, en 15 archivos, uno por grupo). Las genera `tools/generar_http.py`: no se editan a mano. Los cuerpos traen solo los campos obligatorios con datos ficticios; los opcionales se listan en un comentario `# opcionales:`. |
| `flujos/` | Recorridos escritos a mano, en orden. `01-practica.http`: empresa de práctica (emitir, ver, anular, reiniciar) sin SII ni certificado real. |

Regenerar `api/` cuando cambie una ruta (con la instancia local arriba):

```bash
python tools/generar_http.py
```

## Cuidado

- Algunas operaciones **gastan folios o hablan con el SII** (`dte/emitir`, `dte/enviar`,
  `estado/*`, `onboarding/*`, `keystore/negocios/*/timbraje`, `mipyme/*`). En una instancia con
  certificado y CAF reales, `SII_AMBIENTE` decide si va a certificación o a producción (L1 de
  [`../docs/CONSTITUCION.md`](../docs/CONSTITUCION.md)).
- Los archivos de ejemplo para subir (`< ../../tests/dorado/fixtures/...`) son el CAF y el
  certificado **ficticios** del corpus dorado. Sirven para practicar; el SII no los acepta.
- No pongas claves en los `.http`: van en `http/.env`.
