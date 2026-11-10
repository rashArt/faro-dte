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

| Método | Ruta | Para qué |
|---|---|---|
| `POST` | `/api/v1/keystore/certificados` | Subir un certificado (multipart: `archivo`, `password`, `nombre`) |
| `GET` | `/api/v1/keystore/certificados` | Listar certificados (sin secretos) |
| `DELETE` | `/api/v1/keystore/certificados/{cert_id}` | Eliminar un certificado |
| `POST` | `/api/v1/keystore/negocios` | Crear la empresa emisora (form: `rut`, `cert_id`, `ambiente`) |
| `GET` | `/api/v1/keystore/negocios` | Listar empresas con sus datos de emisor |
| `PUT` | `/api/v1/keystore/negocios/{id}` | Datos del emisor (giro, dirección, código de actividad…) |
| `POST` | `/api/v1/caf` | Cargar un CAF (multipart: `archivo`) |
| `GET` | `/api/v1/caf` | Listar CAF cargados |
| `GET` | `/api/v1/caf/activo/{rut}/{tipo}` | CAF en uso para un tipo |
| `GET` | `/api/v1/caf/folios/siguiente/{rut}/{tipo}` | Siguiente folio y rango del CAF |

Ver [Configurar la empresa](../guias/configurar-empresa.md).

## Cuenta y token

| Método | Ruta | Para qué |
|---|---|---|
| `GET` | `/api/v1/auth/me` | Quién soy: tipo, `id`, nombre y rol del token |
| `POST` | `/api/v1/apikeys/propia/renovar` | Renovar el token con que se llama (la clave anterior muere en el acto) |

## Utilidades

| Método | Ruta | Para qué |
|---|---|---|
| `GET` | `/api/v1/valores` | UF, UTM y dólar vigentes (`?fecha=AAAA-MM-DD`) |
| `GET` | `/api/v1/monitoreo/folios?rut=…` | Folios restantes por tipo |

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
