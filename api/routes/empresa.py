"""
api/routes/empresa.py — La empresa de la instancia (docs/deploy/PLAN.md, fase 7).

Una instancia atiende a una sola empresa, así que estas rutas no llevan id ni RUT:

    GET  /api/v1/empresa               datos, ambiente, certificado y qué falta para producción
    PUT  /api/v1/empresa               crear la empresa o actualizar sus datos
    PUT  /api/v1/empresa/certificado   subir el certificado con que firma
    PUT  /api/v1/empresa/ambiente      pasar de demo a certificación y a producción (o volver)
    GET  /api/v1/empresa/folios        folios disponibles y vencimiento de cada CAF

Leer lo puede cualquier credencial (el administrador o un token de integración); cambiar algo,
solo el administrador: los tres PUT responden 403 a un token. Reemplazan, para una instalación
de servidor, a `/api/v1/keystore/negocios/*` (que sigue para la interfaz heredada).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from pydantic import BaseModel, Field

from api.util import leer_upload_sincrono
from core import empresa
from core.auth import requerir_administrador, requerir_autenticacion
from core.autorizacion import actor

router = APIRouter(prefix="/api/v1/empresa", tags=["Empresa"])


class EmpresaIn(BaseModel):
    """Los datos que van en la carátula de cada documento. `rut` solo hace falta para crear la
    empresa. En modo demo (`simulacion`) no se consulta al SII: envía también `razon_social`. En
    certificación o producción la razón social se completa desde el registro del SII. Los campos
    ausentes no se tocan. Largos: los del XSD del SII."""
    rut: Optional[str] = Field(None, description="RUT de la empresa, ej: 76111111-6 (solo al crearla)")
    razon_social: Optional[str] = Field(None, max_length=100)
    giro: Optional[str] = Field(None, max_length=80)
    codigo_actividad: Optional[int] = None
    direccion: Optional[str] = Field(None, max_length=70)
    comuna: Optional[str] = Field(None, max_length=20)
    ciudad: Optional[str] = Field(None, max_length=20)
    email: Optional[str] = Field(None, max_length=80)
    telefono: Optional[str] = Field(None, max_length=20)


class AmbienteIn(BaseModel):
    ambiente: str = Field(..., description="simulacion (demo) | certificacion | produccion")
    confirmar_rut: str = Field(..., description="El RUT de la empresa, repetido: confirma que el cambio es a propósito")


@router.get("", summary="La empresa de la instancia: datos, ambiente, certificado y qué falta para producción")
def ver_empresa(
    consultar_sii: bool = Query(False, description="Comprobar también en el SII si la empresa está "
                                "autorizada en producción (consulta en línea)"),
    usuario: dict = Depends(requerir_autenticacion),
) -> dict:
    return empresa.estado(consultar_sii=consultar_sii)


@router.put("", summary="Crear la empresa o actualizar sus datos (solo administrador)")
def guardar_empresa(body: EmpresaIn, usuario: dict = Depends(requerir_administrador)) -> dict:
    """La primera vez crea la empresa (hace falta `rut`); después actualiza sus datos. Con el RUT
    de otra empresa responde 409: cada empresa tiene su propia instancia. La empresa nace en el
    ambiente inicial de la instancia (`SII_AMBIENTE`): en demo (`simulacion`) basta un RUT bien
    formado; en certificación o producción el RUT debe estar en el registro del SII."""
    datos = body.model_dump(exclude_unset=True, exclude={"rut"})
    empresa.guardar(usuario["id"], body.rut, datos)
    return empresa.estado()


@router.put("/certificado", summary="Subir el certificado digital con que firma la empresa (solo administrador)")
def subir_certificado(
    archivo: UploadFile = File(..., description="Certificado .p12 / .pfx"),
    password: str = Form(..., description="Contraseña del certificado"),
    nombre: Optional[str] = Form(None, description="Nombre descriptivo (opcional)"),
    usuario: dict = Depends(requerir_administrador),
) -> dict:
    """Se valida con su contraseña y se guarda cifrado (L6). Devuelve sus metadatos, nunca el
    archivo ni la contraseña. Subir otro lo reemplaza."""
    return empresa.guardar_certificado(leer_upload_sincrono(archivo), password, nombre)


@router.put("/ambiente", summary="Cambiar el ambiente de la empresa: demo, certificación o producción (solo administrador)")
def cambiar_ambiente(body: AmbienteIn, usuario: dict = Depends(requerir_administrador)) -> dict:
    """Desde el cambio, lo que se emite sale con los CAF y la numeración del ambiente nuevo. Lo
    emitido antes queda en su ambiente. Salir de demo (`simulacion`) a certificación valida el
    RUT en el SII: si no lo conoce responde 409 `requisitos_certificacion`. Pasar a producción
    exige datos de la empresa completos, certificado vigente, un CAF de producción, secretos
    seguros y la autorización del SII: si falta algo responde 409 `requisitos_produccion` con la
    lista. Volver a certificación o a demo no pide requisitos."""
    resultado = empresa.cambiar_ambiente(body.ambiente, body.confirmar_rut, actor(usuario))
    return {**resultado, "empresa": empresa.estado()}


@router.get("/folios", summary="Folios disponibles por tipo de documento y vencimiento de cada CAF")
def ver_folios(
    ambiente: Optional[str] = Query(None, description="simulacion | certificacion | produccion (por defecto, el actual)"),
    usuario: dict = Depends(requerir_autenticacion),
) -> dict:
    return empresa.folios(ambiente)
