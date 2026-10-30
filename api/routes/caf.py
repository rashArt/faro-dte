"""
api/routes/caf.py

Endpoints para gestión de CAF (Código de Autorización de Folios).
"""

from fastapi import APIRouter, UploadFile, File
from api.util import leer_upload_sincrono
from pydantic import BaseModel
from typing import Annotated

from core.caf import ManejadorCAF
from core.errors import CAFError, ValidacionError

router = APIRouter(prefix="/api/v1/caf", tags=["CAF - Folios"])


class CAFInfoResponse(BaseModel):
    """Información del CAF cargado."""
    tipo_dte: int
    rut_emisor: str
    folio_desde: int
    folio_hasta: int
    fecha_autorizacion: str
    total_folios: int
    mensaje: str


class ValidarFolioRequest(BaseModel):
    """Request para validar un folio contra un CAF en Base64."""
    caf_xml_b64: str
    folio: int


class ValidarFolioResponse(BaseModel):
    """Respuesta de validación de folio."""
    folio: int
    valido: bool
    mensaje: str


@router.post(
    "/info",
    response_model=CAFInfoResponse,
    summary="Obtener información de un archivo CAF",
    description="""
    Carga un archivo CAF XML entregado por el SII y retorna su información:
    tipo de DTE, rango de folios autorizados y fecha de autorización.

    El CAF es el archivo que el SII entrega al contribuyente para que pueda
    numerar y timbrar sus documentos tributarios electrónicos.
    """,
)
def info_caf(
    archivo: Annotated[UploadFile, File(description="Archivo CAF en formato XML")],
) -> CAFInfoResponse:
    """
    Retorna la información de un archivo CAF.
    """
    if not archivo.filename or not archivo.filename.lower().endswith(".xml"):
        raise ValidacionError("El archivo debe ser un CAF en formato XML.")

    contenido = leer_upload_sincrono(archivo)

    try:
        caf = ManejadorCAF(contenido)
    except ValueError as e:
        raise CAFError(f"No se pudo parsear el CAF: {e}")

    d = caf.datos
    total_folios = d.folio_hasta - d.folio_desde + 1

    return CAFInfoResponse(
        tipo_dte=d.tipo_dte,
        rut_emisor=d.rut_emisor,
        folio_desde=d.folio_desde,
        folio_hasta=d.folio_hasta,
        fecha_autorizacion=d.fecha_autorizacion.isoformat(),
        total_folios=total_folios,
        mensaje=f"CAF válido para {total_folios} folios del tipo DTE {d.tipo_dte}.",
    )


@router.post(
    "/validar-folio",
    response_model=ValidarFolioResponse,
    summary="Validar si un folio está autorizado en un CAF",
)
def validar_folio(body: ValidarFolioRequest) -> ValidarFolioResponse:
    """
    Verifica si un número de folio está dentro del rango autorizado por el CAF.
    """
    import base64
    try:
        caf_bytes = base64.b64decode(body.caf_xml_b64)
        caf = ManejadorCAF(caf_bytes)
    except Exception as e:
        raise CAFError(f"No se pudo procesar el CAF: {e}")

    es_valido = caf.es_folio_valido(body.folio)

    return ValidarFolioResponse(
        folio=body.folio,
        valido=es_valido,
        mensaje=(
            f"El folio {body.folio} está autorizado en el CAF."
            if es_valido
            else f"El folio {body.folio} está FUERA del rango [{caf.datos.folio_desde}-{caf.datos.folio_hasta}] del CAF."
        ),
    )


# ---- CAF registrados y folios (antes bajo /api/v1/db, fase 4 de docs/deploy/PLAN.md) ----
# Misma implementación que el alias obsoleto `/api/v1/db/*` (api/routes/db.py), que Go y Rust
# siguen sirviendo y `tools/contrato_api.py` compara.
from api.routes import db as _db  # noqa: E402

router.add_api_route("", _db.get_cafs, methods=["GET"], response_model=list[_db.CAFResponse],
                     summary="Listar CAF registrados")
router.add_api_route("", _db.post_registrar_caf, methods=["POST"], response_model=_db.CAFResponse,
                     summary="Registrar un CAF subiendo el XML del SII")
router.add_api_route("/activo/{rut_emisor}/{tipo_dte}", _db.get_caf_activo, methods=["GET"],
                     response_model=_db.CAFResponse, summary="CAF activo de un emisor y tipo")
router.add_api_route("/folios/siguiente/{rut_emisor}/{tipo_dte}", _db.get_siguiente_folio, methods=["GET"],
                     response_model=_db.FolioDisponibleResponse, summary="Siguiente folio disponible")
