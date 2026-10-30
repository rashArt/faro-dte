"""
core/auth.py

Módulo de autenticación JWT para proteger los endpoints de la API.

Fase 4 (docs/deploy/PLAN.md): `requerir_autenticacion` resuelve la credencial una vez por petición
(la guarda en `request.state`, con su `actor` para la auditoría), aplica los permisos del token
(`core/autorizacion.py`) y cuenta los Bearer inválidos (`core/limites.py`). Un JWT emitido antes
del último cambio de contraseña ya no vale.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext

from core import autorizacion, limites
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


_HASH_SEÑUELO: Optional[str] = None


def verificar_sin_revelar(plain_password: str, usuario: Optional[dict]) -> bool:
    """Como `verify_password`, pero tarda lo mismo exista o no el usuario: sin usuario se verifica
    contra un hash señuelo, así el tiempo de respuesta no dice qué usuarios existen."""
    global _HASH_SEÑUELO
    if usuario:
        return verify_password(plain_password, usuario["hashed_password"])
    if _HASH_SEÑUELO is None:
        _HASH_SEÑUELO = hash_password(secrets.token_urlsafe(16))
    verify_password(plain_password, _HASH_SEÑUELO)
    return False


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
    ahora = datetime.now(timezone.utc)
    expire = ahora + (
        expires_delta or timedelta(minutes=settings.jwt_access_token_expire_minutes)
    )
    to_encode.update({"exp": expire, "iat": int(ahora.timestamp())})
    return jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def token_de_usuario(usuario: dict) -> str:
    """El JWT de un usuario. Lleva la fecha de su último cambio de contraseña (`cc`): al cambiarla,
    los JWT anteriores dejan de coincidir y no valen más (exacto, sin depender del reloj)."""
    datos = {"sub": usuario["username"]}
    if usuario.get("clave_cambiada_en"):
        datos["cc"] = usuario["clave_cambiada_en"]
    return crear_access_token(data=datos)


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
    # Emitido antes del último cambio de contraseña (`token_de_usuario`): ya no vale.
    if usuario and payload.get("cc") != usuario.get("clave_cambiada_en"):
        return None
    return usuario


def _usuario_dueno() -> Optional[dict]:
    """El usuario dueño de la instalación: el de menor id (la cuenta es única, ver
    docs/PLATAFORMA.md). Es con quien actúa la app de escritorio."""
    from core.models import listar_usuarios
    usuarios = sorted(listar_usuarios(), key=lambda u: u["id"])
    return usuarios[0] if usuarios else None


def ip_cliente(request: Request) -> str:
    """La IP de quien llama. Detrás de un proxy, uvicorn la toma de `X-Forwarded-For` solo si
    corre con `--proxy-headers` y el proxy está en `--forwarded-allow-ips`."""
    return request.client.host if request.client else "-"


def _resolver(credentials: Optional[HTTPAuthorizationCredentials]) -> Optional[dict]:
    """El usuario o token de la credencial, o None si no es válida."""
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
            # La instancia es de una empresa: el token opera sobre la cuenta dueña (sus negocios,
            # certificados y claves del SII), como la clave de la app. `id` es esa cuenta, porque
            # las rutas del keystore la toman de ahí; quién actúa sale de `api_key_id`.
            dueno = _usuario_dueno()
            return {"id": dueno["id"] if dueno else None, "username": f"apikey:{rec['nombre']}",
                    "nombre": rec["nombre"], "tipo": "apikey", "api_key_id": rec["id"], "rol": rec["rol"]}
    return None


def requerir_autenticacion(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> dict:
    """
    Dependencia de FastAPI que REQUIERE autenticación: acepta un **JWT** (usuario) o una
    **API key** (`dte_...`, para integraciones/agentes). Lanza HTTP 401 si no hay ninguno válido,
    429 si la IP ya mandó demasiados inválidos, y 403 si un token pide algo fuera de su rol.
    """
    usuario = getattr(request.state, "credencial", None)
    if usuario is None:
        usuario = _resolver(credentials)
        if usuario is None:
            if credentials:
                espera = limites.bearer_fallido(ip_cliente(request))
                if espera:
                    raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                                        detail="Demasiadas credenciales inválidas desde esta IP; espera y reintenta.",
                                        headers={"Retry-After": str(espera)})
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Se requiere un JWT o una API key válidos (Authorization: Bearer).",
                headers={"WWW-Authenticate": "Bearer"},
            )
        request.state.credencial = usuario
        request.state.actor = autorizacion.actor(usuario)
    if autorizacion.es_token(usuario):
        ruta = getattr(request.scope.get("route"), "path", request.url.path)
        if not autorizacion.permite_integracion(request.method, ruta):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="Un token de integración no puede usar esta ruta.")
    return usuario


def es_administrador(usuario: Optional[dict]) -> bool:
    """Rol `administrador`: todo usuario con login (y la clave de la app, que actúa como el dueño).
    Un token nunca lo es: su rol es `integracion` (`core/autorizacion.py`)."""
    return bool(usuario) and not autorizacion.es_token(usuario) and usuario.get("id") is not None


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
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> dict:
    """Como `requerir_autenticacion`, pero solo el administrador (403 para el resto)."""
    usuario = requerir_autenticacion(request, credentials)
    if not es_administrador(usuario):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Esta acción es solo para el administrador de la instancia.")
    return usuario


def _es_clave_app(credentials: Optional[HTTPAuthorizationCredentials]) -> bool:
    return bool(credentials and settings.app_shell_key and len(settings.app_shell_key) >= 24
                and secrets.compare_digest(credentials.credentials, settings.app_shell_key))


def requerir_clave_app(
    request: Request,
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
    request.state.actor = autorizacion.actor(dueno)
    return dueno


def requerir_usuario(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> dict:
    """Como `requerir_autenticacion` pero SOLO acepta un usuario real (JWT), no API keys. Para
    acciones sensibles como cambiar la propia contraseña."""
    usuario = obtener_usuario_actual(credentials)
    if not usuario:
        from core.apikeys import verificar_api_key
        if credentials and verificar_api_key(credentials.credentials):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="Un token de integración no puede usar esta ruta.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Esta acción requiere un usuario autenticado (JWT), no una API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    actualizar_ultimo_acceso(usuario["id"])
    request.state.actor = autorizacion.actor(usuario)
    return usuario
