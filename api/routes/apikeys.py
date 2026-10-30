"""
api/routes/apikeys.py — Gestión de API keys (bearer estáticas para integraciones/agentes).

Crear/listar/renovar/revocar: solo el **administrador con login** (JWT; ni un token ni la clave
de la app, como en el motor Go), fase 4 de docs/deploy/PLAN.md. Un token
solo puede renovarse a sí mismo (`POST /apikeys/propia/renovar`). Renovar cambia el secreto,
conserva el `id` (y con él el `id_externo` y el rastro en la auditoría) y la clave anterior muere
al instante, igual que al revocar. La clave en claro se devuelve **una sola vez**.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from core import apikeys
from core.auth import requerir_autenticacion, requerir_usuario
from core.autorizacion import es_token

router = APIRouter(prefix="/api/v1/apikeys", tags=["API Keys"])

_AVISO = "Guarda esta clave AHORA; no se vuelve a mostrar."


class CrearKeyRequest(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=80, description="Nombre para identificar la key")


@router.post("", status_code=status.HTTP_201_CREATED, summary="Crear una API key")
def crear(body: CrearKeyRequest, usuario: dict = Depends(requerir_usuario)) -> dict:
    """Crea una API key nueva (rol `integracion`). Devuelve la clave en claro **una sola vez** — guárdala ya."""
    clave, reg = apikeys.crear_api_key(body.nombre.strip(), creado_por=usuario["id"])
    return {"api_key": clave, "aviso": _AVISO, **reg}


@router.get("", summary="Listar API keys")
def listar(usuario: dict = Depends(requerir_usuario)) -> list:
    """Lista las keys (sin exponer la clave: solo el prefijo, rol, estado y uso)."""
    return apikeys.listar_api_keys()


def _renovada(key_id: int) -> dict:
    resultado = apikeys.renovar_api_key(key_id)
    if not resultado:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "API key no encontrada o revocada")
    clave, reg = resultado
    return {"api_key": clave, "aviso": _AVISO + " La clave anterior ya no sirve.", **reg}


# Antes que `/{key_id}/renovar`: si no, "propia" se toma por un id y responde 422.
@router.post("/propia/renovar", summary="Renovar el token con que se llama (solo tokens)")
def renovar_propia(usuario: dict = Depends(requerir_autenticacion)) -> dict:
    """El token que hace la llamada recibe un secreto nuevo; el que usó deja de servir ya.
    Conserva su `id`, así sus `id_externo` siguen siendo suyos."""
    if not es_token(usuario):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Esta ruta es para un token; el administrador usa /apikeys/{id}/renovar.")
    return _renovada(usuario["api_key_id"])


@router.post("/{key_id}/renovar", summary="Renovar una API key")
def renovar(key_id: int, usuario: dict = Depends(requerir_usuario)) -> dict:
    """Secreto nuevo para la key, mismo `id`. La clave anterior deja de servir en el acto."""
    return _renovada(key_id)


@router.delete("/{key_id}", summary="Revocar una API key")
def revocar(key_id: int, usuario: dict = Depends(requerir_usuario)) -> dict:
    if not apikeys.revocar_api_key(key_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "API key no encontrada")
    return {"revocada": key_id}
