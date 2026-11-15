"""
api/routes/db.py

Endpoints para consultar y gestionar datos persistentes:
- Listar DTEs emitidos
- Gestionar CAFs
- Consultar logs de auditoría
- Obtener siguiente folio disponible
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Form, Query, UploadFile, File
from api.util import leer_upload_sincrono
from pydantic import BaseModel, Field

from core.ambiente import AMBIENTES, ambiente_actual
from core.auth import requerir_administrador, requerir_autenticacion
from core.database import obtenerUno
from core.caf import ManejadorCAF
from core.errors import CAFError, ConflictoError, RecursoNoEncontrado, ValidacionError
from core.models import (
    listar_dtes,
    contar_dtes,
    obtener_dte,
    obtener_dte_por_folio,
    caf_vencido,
    listar_cafs,
    obtener_caf_activo,
    usar_caf,
    vence_caf,
    obtener_siguiente_folio,
    registrar_caf,
    listar_logs,
)

router = APIRouter(prefix="/api/v1/db", tags=["Base de Datos"])


# ---- Modelos de respuesta ----

class DTEResponse(BaseModel):
    id: int
    tipo_dte: int
    folio: int
    rut_emisor: str
    rut_receptor: str
    razon_social_receptor: Optional[str]
    fecha_emision: str
    monto_neto: int
    monto_exento: int
    iva: int
    monto_total: int
    estado: str
    track_id: Optional[int]
    pdf_path: Optional[str]
    ambiente: str
    creado_en: str


class DTEListResponse(BaseModel):
    total: int
    dtes: list[DTEResponse]


class CAFResponse(BaseModel):
    id: int
    tipo_dte: int
    rut_emisor: str
    folio_desde: int
    folio_hasta: int
    folio_siguiente: int
    fecha_autorizacion: str
    activo: bool
    creado_en: str
    # Fase 7 (fork): ambiente del CAF, si es el que está en uso y hasta cuándo sirve.
    ambiente: Optional[str] = None
    en_uso: bool = False
    vence: Optional[str] = None
    vigente: bool = True


def _caf(c: dict) -> CAFResponse:
    vence = vence_caf(c["fecha_autorizacion"])
    return CAFResponse(
        id=c["id"], tipo_dte=c["tipo_dte"], rut_emisor=c["rut_emisor"], folio_desde=c["folio_desde"],
        folio_hasta=c["folio_hasta"], folio_siguiente=c["folio_siguiente"],
        fecha_autorizacion=c["fecha_autorizacion"], activo=bool(c["activo"]), creado_en=c["creado_en"],
        ambiente=c.get("ambiente"), en_uso=bool(c.get("en_uso")),
        vence=vence.isoformat() if vence else None, vigente=not caf_vencido(c))


class FolioDisponibleResponse(BaseModel):
    rut_emisor: str
    tipo_dte: int
    folio_disponible: Optional[int]
    folio_desde: Optional[int]
    folio_hasta: Optional[int]
    mensaje: str


class LogResponse(BaseModel):
    id: int
    accion: str
    tipo_dte: Optional[int]
    folio: Optional[int]
    rut_emisor: Optional[str]
    detalle: Optional[str]
    creado_en: str


class RegistrarCAFRequest(BaseModel):
    tipo_dte: int
    rut_emisor: str
    folio_desde: int
    folio_hasta: int
    fecha_autorizacion: str
    caf_xml: str


# ---- Endpoints DTEs ----

@router.get("/dtes", response_model=DTEListResponse, summary="Listar DTEs emitidos")
def get_dtes(
    rut_emisor: Optional[str] = Query(None, description="Filtrar por RUT emisor"),
    tipo_dte: Optional[int] = Query(None, description="Filtrar por tipo DTE"),
    estado: Optional[str] = Query(None, description="Filtrar por estado"),
    fecha_desde: Optional[str] = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    fecha_hasta: Optional[str] = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
    limite: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    usuario: dict = Depends(requerir_autenticacion),
) -> DTEListResponse:
    """Lista los DTEs emitidos con filtros opcionales."""
    dtes = listar_dtes(
        rut_emisor=rut_emisor,
        tipo_dte=tipo_dte,
        estado=estado,
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
        limite=limite,
        offset=offset,
    )
    total = contar_dtes(rut_emisor=rut_emisor, tipo_dte=tipo_dte, estado=estado)

    return DTEListResponse(
        total=total,
        dtes=[DTEResponse(**d) for d in dtes],
    )


@router.get("/dtes/{dte_id}", response_model=DTEResponse, summary="Obtener DTE por ID")
def get_dte(
    dte_id: int,
    usuario: dict = Depends(requerir_autenticacion),
) -> DTEResponse:
    """Obtiene un DTE específico por su ID."""
    dte = obtener_dte(dte_id)
    if not dte:
        raise RecursoNoEncontrado("DTE no encontrado")
    return DTEResponse(**dte)


@router.get(
    "/dtes/buscar/{rut_emisor}/{tipo_dte}/{folio}",
    response_model=DTEResponse,
    summary="Buscar DTE por emisor, tipo y folio",
)
def buscar_dte(
    rut_emisor: str,
    tipo_dte: int,
    folio: int,
    usuario: dict = Depends(requerir_autenticacion),
) -> DTEResponse:
    """Busca un DTE por RUT emisor, tipo y folio."""
    dte = obtener_dte_por_folio(rut_emisor, tipo_dte, folio)
    if not dte:
        raise RecursoNoEncontrado("DTE no encontrado")
    return DTEResponse(**dte)


# ---- Endpoints CAFs ----

@router.get("/cafs", response_model=list[CAFResponse], summary="Listar CAFs registrados")
def get_cafs(
    rut_emisor: Optional[str] = Query(None),
    tipo_dte: Optional[int] = Query(None),
    ambiente: Optional[str] = Query(None, description="simulacion | certificacion | produccion (por defecto, todos)"),
    usuario: dict = Depends(requerir_autenticacion),
) -> list[CAFResponse]:
    """Lista los CAFs registrados, de todos los ambientes salvo que se pida uno."""
    cafs = listar_cafs(rut_emisor=rut_emisor, tipo_dte=tipo_dte, ambiente=ambiente)
    return [
        _caf(c)
        for c in cafs
    ]


@router.get(
    "/cafs/activo/{rut_emisor}/{tipo_dte}",
    response_model=CAFResponse,
    summary="Obtener CAF activo",
)
def get_caf_activo(
    rut_emisor: str,
    tipo_dte: int,
    usuario: dict = Depends(requerir_autenticacion),
) -> CAFResponse:
    """Obtiene el CAF activo para un emisor y tipo de DTE."""
    caf = obtener_caf_activo(rut_emisor, tipo_dte)
    if not caf:
        raise RecursoNoEncontrado("No hay CAF activo para estos parámetros")
    return _caf(caf)


@router.post(
    "/cafs",
    response_model=CAFResponse,
    summary="Registrar un nuevo CAF subiendo el XML",
    description="Sube un archivo CAF XML descargado del SII y lo registra de forma persistente en la BD.",
)
def post_registrar_caf(
    archivo: UploadFile = File(..., description="Archivo CAF XML"),
    ambiente: Optional[str] = Form(None, description="simulacion | certificacion | produccion. Por defecto, el "
                                   "ambiente actual de la empresa. Sirve para cargar el CAF de producción "
                                   "antes de cambiar de ambiente."),
    usuario: dict = Depends(requerir_autenticacion),
) -> CAFResponse:
    """Registra un nuevo CAF leyendo sus metadatos del XML."""
    if not archivo.filename or not archivo.filename.lower().endswith(".xml"):
        raise ValidacionError("El archivo debe ser un CAF en formato XML.")

    contenido = leer_upload_sincrono(archivo)

    try:
        caf = ManejadorCAF(contenido)
    except (ValueError, SyntaxError) as e:   # SyntaxError: XML mal formado (lxml)
        raise CAFError(f"No se pudo parsear el CAF: {e}")

    d = caf.datos

    # Los CAF anteriores del mismo tipo siguen activos: sus folios se usan hasta agotarse o
    # vencer, y después el motor pasa solo a este (core/models.py::_elegir_caf).
    ambiente = (ambiente or ambiente_actual()).strip().lower()
    if ambiente not in AMBIENTES:
        raise ValidacionError("`ambiente` debe ser simulacion, certificacion o produccion.",
                              detalle={"ambiente": ambiente})
    fecha = d.fecha_autorizacion.isoformat()
    if caf_vencido({"ambiente": ambiente, "fecha_autorizacion": fecha}):
        raise CAFError(f"El CAF venció el {vence_caf(fecha)} (6 meses desde su autorización, {fecha[:10]}): "
                       "sus folios ya no se pueden usar. Pide folios nuevos al SII.",
                       detalle={"fecha_autorizacion": fecha[:10], "vence": str(vence_caf(fecha))})

    if obtenerUno("SELECT 1 FROM cafs WHERE rut_emisor=? AND tipo_dte=? AND folio_desde=? AND ambiente=?",
                  (d.rut_emisor, d.tipo_dte, d.folio_desde, ambiente)):
        raise ConflictoError(
            f"Ya hay un CAF del tipo {d.tipo_dte} que parte en el folio {d.folio_desde} en {ambiente}: "
            "no se carga dos veces.", codigo="caf_repetido",
            detalle={"tipo_dte": d.tipo_dte, "folio_desde": d.folio_desde, "ambiente": ambiente})

    # Fallo al guardar → 500 vía el handler global (traza al log).
    caf_id = registrar_caf(
        tipo_dte=d.tipo_dte,
        rut_emisor=d.rut_emisor,
        folio_desde=d.folio_desde,
        folio_hasta=d.folio_hasta,
        fecha_autorizacion=fecha,
        caf_xml=contenido.decode("utf-8", errors="ignore"),
        ambiente=ambiente,
    )
    return _caf(dict(obtenerUno("SELECT * FROM cafs WHERE id=?", (caf_id,))))


def put_usar_caf(caf_id: int, usuario: dict = Depends(requerir_administrador)) -> CAFResponse:
    """Fija el CAF del que salen los folios de su tipo y ambiente. Sin esto, el motor usa el
    vigente que vence primero y pasa solo al siguiente cuando se agota o vence."""
    return _caf(usar_caf(caf_id))


@router.get(
    "/folios/siguiente/{rut_emisor}/{tipo_dte}",
    response_model=FolioDisponibleResponse,
    summary="Obtener siguiente folio disponible",
)
def get_siguiente_folio(
    rut_emisor: str,
    tipo_dte: int,
    usuario: dict = Depends(requerir_autenticacion),
) -> FolioDisponibleResponse:
    """Obtiene el siguiente folio disponible para emitir."""
    folio = obtener_siguiente_folio(rut_emisor, tipo_dte)
    caf = obtener_caf_activo(rut_emisor, tipo_dte)

    if not caf:
        return FolioDisponibleResponse(
            rut_emisor=rut_emisor,
            tipo_dte=tipo_dte,
            folio_disponible=None,
            folio_desde=None,
            folio_hasta=None,
            mensaje="No hay CAF activo. Debe registrar un CAF primero.",
        )

    if folio is None:
        return FolioDisponibleResponse(
            rut_emisor=rut_emisor,
            tipo_dte=tipo_dte,
            folio_disponible=None,
            folio_desde=caf["folio_desde"],
            folio_hasta=caf["folio_hasta"],
            mensaje="Rango de folios agotado. Debe solicitar nuevos folios al SII.",
        )

    return FolioDisponibleResponse(
        rut_emisor=rut_emisor,
        tipo_dte=tipo_dte,
        folio_disponible=folio,
        folio_desde=caf["folio_desde"],
        folio_hasta=caf["folio_hasta"],
        mensaje=f"Siguiente folio disponible: {folio}",
    )


# ---- Endpoints Logs ----

@router.get("/logs", response_model=list[LogResponse], summary="Consultar logs de auditoría")
def get_logs(
    accion: Optional[str] = Query(None),
    rut_emisor: Optional[str] = Query(None),
    fecha_desde: Optional[str] = Query(None),
    limite: int = Query(100, ge=1, le=1000),
    usuario: dict = Depends(requerir_autenticacion),
) -> list[LogResponse]:
    """Consulta los logs de auditoría."""
    logs = listar_logs(
        accion=accion,
        rut_emisor=rut_emisor,
        fecha_desde=fecha_desde,
        limite=limite,
    )
    return [
        LogResponse(
            id=log["id"],
            accion=log["accion"],
            tipo_dte=log.get("tipo_dte"),
            folio=log.get("folio"),
            rut_emisor=log.get("rut_emisor"),
            detalle=log.get("detalle"),
            creado_en=log["creado_en"],
        )
        for log in logs
    ]
