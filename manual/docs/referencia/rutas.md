# Rutas

Todas las rutas cuelgan de la URL de la instancia (por ejemplo `http://faro-acme:8000`). Piden
`Authorization: Bearer <token>`, salvo las de la consulta pública.

## Documentos

| Método | Ruta | Para qué | Guía |
|---|---|---|---|
| `POST` | `/api/v1/dte/emitir` | Emitir un documento | [Emitir](../guias/emitir.md) |
| `POST` | `/api/v1/dte/previsualizar` | Borrador sin gastar folio | [Previsualizar](../guias/previsualizar.md) |
| `GET` | `/api/v1/dte/{dte_id}/estado` | Estado ante el SII (`?consultar_sii=true` para preguntar ahora) | [Seguir el veredicto](../guias/seguir-veredicto.md) |
| `POST` | `/api/v1/dte/{dte_id}/enviar` | Enviar ahora, o reintentar un `envio_fallido` | [Enviar tú mismo](../guias/envio-manual.md) |
| `GET` | `/api/v1/dte/documentos/{dte_id}` | Datos de un documento | [Consultar](../guias/consultar.md) |
| `GET` | `/api/v1/dte/documentos/buscar` | Buscar por `rut_emisor`, `tipo_dte` y `folio` | [Consultar](../guias/consultar.md) |
| `GET` | `/api/v1/dte/documentos/{dte_id}/pdf` | PDF con timbre | [Consultar](../guias/consultar.md) |
| `GET` | `/api/v1/dte/documentos/{dte_id}/xml` | XML firmado | [Consultar](../guias/consultar.md) |

## Configuración de la empresa

Una instancia atiende a una sola empresa: estas rutas no llevan id ni RUT. Las marcadas con
**A** son solo del administrador: un token de integración recibe `403`. Las demás las usa
cualquier credencial.

| Método | Ruta | Para qué |
|---|---|---|
| `GET` | `/api/v1/empresa` | Datos, ambiente, certificado y qué falta para producción (`?consultar_sii=true` pregunta al SII) |
| `PUT` | `/api/v1/empresa` | **A** · Crear la empresa (`rut`) o actualizar sus datos (razón social, giro, dirección, código de actividad…) |
| `PUT` | `/api/v1/empresa/certificado` | **A** · Subir el certificado (multipart: `archivo`, `password`, `nombre`) |
| `PUT` | `/api/v1/empresa/ambiente` | **A** · Cambiar de ambiente: `simulacion` (demo), `certificacion` o `produccion` (`ambiente`, `confirmar_rut`) |
| `GET` | `/api/v1/empresa/folios` | Folios disponibles por tipo y vencimiento de cada CAF (`?ambiente=`) |
| `POST` | `/api/v1/caf` | Cargar un CAF (multipart: `archivo`; `ambiente` opcional) |
| `GET` | `/api/v1/caf` | Listar CAF cargados (`?ambiente=`, `?tipo_dte=`) |
| `PUT` | `/api/v1/caf/{id}/en-uso` | **A** · Fijar el CAF del que salen los folios de su tipo |

Ver [Configurar la empresa](../guias/configurar-empresa.md) y
[Pasar a producción](../guias/pasar-a-produccion.md).

## Cuenta y token

| Método | Ruta | Para qué |
|---|---|---|
| `GET` | `/api/v1/auth/me` | Quién soy: tipo, `id`, nombre y rol del token |
| `POST` | `/api/v1/apikeys/propia/renovar` | Renovar el token con que se llama (la clave anterior muere en el acto) |

## Utilidades

| Método | Ruta | Para qué |
|---|---|---|
| `GET` | `/api/v1/valores` | UF, UTM y dólar vigentes (`?fecha=AAAA-MM-DD`) |

## Consulta pública (sin token)

| Método | Ruta | Para qué |
|---|---|---|
| `GET` | `/consulta/api` | Verificar una boleta con sus datos reales |
| `GET` | `/consulta/pdf`, `/consulta/xml` | Descargar la boleta verificada |

Ver [Consulta pública de boletas](../guias/consulta-publica.md).

## Solo administrador

Un token de integración recibe `403` en estas rutas. Las usa quien opera la instancia.

| Método | Ruta | Para qué |
|---|---|---|
| `POST` | `/api/v1/auth/login` | Iniciar sesión (usuario y contraseña, entrega un JWT) |
| `POST` | `/api/v1/apikeys` | Crear un token de integración |
| `GET` | `/api/v1/apikeys` | Listar tokens |
| `POST` | `/api/v1/apikeys/{id}/renovar` | Renovar un token |
| `DELETE` | `/api/v1/apikeys/{id}` | Revocar un token |
| `GET` | `/api/v1/dte/documentos` | Listado general con filtros |
| `GET` | `/api/v1/dte/alertas` | Documentos que necesitan atención |
| `GET` | `/api/v1/auditoria` | Quién hizo qué |

!!! note "Rutas antiguas"
    La API conserva algunas rutas por compatibilidad (`/api/v1/dte/enviar`, `/api/v1/estado/track`,
    `/api/v1/db/…`). Una integración nueva no las necesita y no se documentan aquí.
