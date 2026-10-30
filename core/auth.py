"""
core/auth.py

Módulo de autenticación JWT para proteger los endpoints de la API.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext

from core.config import settings
from core.models import obtener_usuario_por_username, actualizar_ultimo_acceso


pwd_context = CryptContext(schemes=["sha256_crypt"], deprecated="auto")
security = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    """Genera el hash de una contraseña."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifica si una contraseña coincide con su hash."""
    return pwd_context.verify(plain_password, hashed_password)


def crear_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Crea un token JWT de acceso.

    Args:
        data: Datos a incluir en el token (ej: {"sub": "username"}).
        expires_delta: Tiempo de expiración personalizado.

    Returns:
        Token JWT como string.
    """
    to_encode = data.copy()
    expire = datetime.utcnow() + (
        expires_delta or timedelta(minutes=settings.jwt_access_token_expire_minutes)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decodificar_token(token: str) -> Optional[dict]:
    """
    Decodifica y valida un token JWT.

    Args:
        token: Token JWT a decodificar.

    Returns:
        Payload del token si es válido, None si no.
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        return payload
    except JWTError:
        return None


def obtener_usuario_actual(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Optional[dict]:
    """
    Dependencia de FastAPI para obtener el usuario actual desde el token JWT.

    Uso:
        @router.get("/protegido")
        def endpoint(usuario=Depends(obtener_usuario_actual)):
            ...
    """
    if not credentials:
        return None

    token = credentials.credentials
    payload = decodificar_token(token)

    if not payload:
        return None

    username = payload.get("sub")
    if not username:
        return None

    usuario = obtener_usuario_por_username(username)
    return usuario


def _usuario_dueno() -> Optional[dict]:
    """El usuario dueño de la instalación: el de menor id (la cuenta es única, ver
    docs/PLATAFORMA.md). Es con quien actúa la app de escritorio."""
    from core.models import listar_usuarios
    usuarios = sorted(listar_usuarios(), key=lambda u: u["id"])
    return usuarios[0] if usuarios else None


def requerir_autenticacion(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> dict:
    """
    Dependencia de FastAPI que REQUIERE autenticación: acepta un **JWT** (usuario) o una
    **API key** (`dte_...`, para integraciones/agentes). Lanza HTTP 401 si no hay ninguno válido.
    """
    usuario = obtener_usuario_actual(credentials)
    if usuario:
        actualizar_ultimo_acceso(usuario["id"])
        return usuario
    # Clave efímera de la app de escritorio (APP_SHELL_KEY, solo por entorno): actúa como el
    # usuario dueño de la cuenta (el primero creado), así los recursos por cuenta (negocios,
    # certificados) son los suyos. Comparación en tiempo constante.
    if _es_clave_app(credentials):
        dueno = _usuario_dueno()
        if dueno:
            actualizar_ultimo_acceso(dueno["id"])
            return dueno
    # No es un JWT válido → probar como API key.
    if credentials:
        from core.apikeys import verificar_api_key
        rec = verificar_api_key(credentials.credentials)
        if rec:
            return {"id": None, "username": f"apikey:{rec['nombre']}",
                    "tipo": "apikey", "api_key_id": rec["id"]}
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Se requiere un JWT o una API key válidos (Authorization: Bearer).",
        headers={"WWW-Authenticate": "Bearer"},
    )


def es_administrador(usuario: Optional[dict]) -> bool:
    """El administrador de la instancia es el usuario dueño (el primero creado, el que arma
    `main.py` al arrancar). Una API key nunca lo es. Los roles llegan en la fase 4
    (docs/deploy/PLAN.md); hasta entonces esta es la regla."""
    if not usuario or usuario.get("tipo") == "apikey" or usuario.get("id") is None:
        return False
    dueno = _usuario_dueno()
    return bool(dueno) and dueno["id"] == usuario["id"]


def solicitante(usuario: dict):
    """El `Solicitante` del servicio de emisión para el usuario autenticado. Una API key emite
    con los negocios y certificados de la cuenta dueña (la instancia es de una empresa) y sus
    `id_externo` son propios de esa key."""
    from core.servicios.emision import Solicitante
    if usuario.get("tipo") == "apikey":
        dueno = _usuario_dueno()
        return Solicitante(origen=f"apikey:{usuario['api_key_id']}",
                           cuenta_id=dueno["id"] if dueno else None, es_admin=False)
    return Solicitante(origen=f"usuario:{usuario['id']}", cuenta_id=usuario["id"],
                       es_admin=es_administrador(usuario))


def requerir_administrador(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> dict:
    """Como `requerir_autenticacion`, pero solo el administrador (403 para el resto)."""
    usuario = requerir_autenticacion(credentials)
    if not es_administrador(usuario):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Esta acción es solo para el administrador de la instancia.")
    return usuario


def _es_clave_app(credentials: Optional[HTTPAuthorizationCredentials]) -> bool:
    return bool(credentials and settings.app_shell_key and len(settings.app_shell_key) >= 24
                and secrets.compare_digest(credentials.credentials, settings.app_shell_key))


def requerir_clave_app(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> dict:
    """Solo la clave efímera de la app de escritorio (`APP_SHELL_KEY`), que actúa como el dueño.

    Para las rutas que entregan secretos en claro al cascarón local (clave privada del
    certificado, claves del SII): ni un JWT ni una API key bastan (hallazgo #8). En un servidor
    sin `APP_SHELL_KEY` responden siempre 401 o 403."""
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Se requiere la clave de la app (Authorization: Bearer).",
                            headers={"WWW-Authenticate": "Bearer"})
    if not _es_clave_app(credentials):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Esta ruta es solo para la app de escritorio.")
    dueno = _usuario_dueno()
    if not dueno:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="La instancia no tiene usuario dueño.")
    return dueno


def requerir_usuario(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> dict:
    """Como `requerir_autenticacion` pero SOLO acepta un usuario real (JWT), no API keys. Para
    acciones sensibles como gestionar las propias API keys (que una key no cree más keys)."""
    usuario = obtener_usuario_actual(credentials)
    if not usuario:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Esta acción requiere un usuario autenticado (JWT), no una API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    actualizar_ultimo_acceso(usuario["id"])
    return usuario
