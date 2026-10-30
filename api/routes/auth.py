"""
api/routes/auth.py

Endpoints de autenticación: login, registro, gestión de usuarios.

Fase 4 (docs/deploy/PLAN.md): todo usuario es `administrador`; los tokens (`integracion`) no
registran, listan ni desactivan usuarios. Login con límite de intentos por usuario+IP
(`core/limites.py`), mismo mensaje y mismo tiempo exista o no el usuario, y cada intento queda en
la auditoría. Cambiar la contraseña invalida los JWT emitidos antes.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator

from core.errors import AutenticacionError, ConflictoError, ValidacionError
from fastapi.security import HTTPAuthorizationCredentials

from core import limites
from core.autorizacion import actor, es_token
from core.config import settings
from core.auth import (
    hash_password,
    ip_cliente,
    verificar_sin_revelar,
    verify_password,
    requerir_administrador,
    requerir_autenticacion,
    requerir_usuario,
    security,
    token_de_usuario,
)
from core.models import (
    cambiar_clave_usuario,
    crear_usuario,
    obtener_usuario_por_username,
    listar_usuarios,
    desactivar_usuario,
    registrar_log,
)

router = APIRouter(prefix="/api/v1/auth", tags=["Autenticación"])


# ---- Modelos de request/response ----

class LoginRequest(BaseModel):
    username: str = Field(..., description="Nombre de usuario")
    password: str = Field(..., description="Contraseña")


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    nombre_completo: str | None = None


def _clave_suficiente(clave: str) -> str:
    if len(clave) < settings.clave_largo_minimo:
        raise ValueError(f"La contraseña debe tener al menos {settings.clave_largo_minimo} caracteres")
    return clave


class RegistroRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50, description="Nombre de usuario")
    password: str = Field(..., description="Contraseña (mínimo CLAVE_LARGO_MINIMO, 12 por defecto)")
    nombre_completo: str | None = Field(None, description="Nombre completo")
    email: str | None = Field(None, description="Email")

    _largo = field_validator("password")(_clave_suficiente)


class CambiarClaveRequest(BaseModel):
    clave_actual: str = Field(..., description="Contraseña actual")
    clave_nueva: str = Field(..., description="Contraseña nueva (mínimo CLAVE_LARGO_MINIMO, 12 por defecto)")

    _largo = field_validator("clave_nueva")(_clave_suficiente)


class RegistroResponse(BaseModel):
    id: int
    username: str
    mensaje: str


class UsuarioResponse(BaseModel):
    id: int
    username: str
    nombre_completo: str | None
    email: str | None
    creado_en: str
    ultimo_acceso: str | None


class MensajeResponse(BaseModel):
    mensaje: str


# ---- Endpoints ----

def _demasiados_intentos(espera: int) -> HTTPException:
    return HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                         detail="Demasiados intentos fallidos; espera y reintenta.",
                         headers={"Retry-After": str(espera)})


def _auditar_acceso(request: Request, accion: str, estado_http: int, detalle: str,
                    usuario: dict | None = None) -> None:
    registrar_log(accion=accion, detalle=detalle, actor=actor(usuario), metodo=request.method,
                  ruta=request.url.path, estado_http=estado_http, ip_origen=ip_cliente(request))


@router.post("/login", response_model=LoginResponse, summary="Iniciar sesión")
def login(body: LoginRequest, request: Request) -> LoginResponse:
    """
    Autentica un usuario y retorna un token JWT.

    El token debe incluirse en el header `Authorization: Bearer <token>`
    para acceder a endpoints protegidos. Tras `LOGIN_MAX_FALLOS` fallos (5) desde la misma IP,
    ese usuario queda bloqueado ahí `LOGIN_BLOQUEO_MIN` minutos (15): responde 429 con `Retry-After`.
    """
    ip = ip_cliente(request)
    intentado = f"Usuario intentado: {body.username[:50]}"
    espera = limites.login_bloqueado(body.username, ip)
    if espera:
        _auditar_acceso(request, "login_bloqueado", 429, intentado)
        raise _demasiados_intentos(espera)

    usuario = obtener_usuario_por_username(body.username)
    if not verificar_sin_revelar(body.password, usuario):
        espera = limites.login_fallido(body.username, ip)
        _auditar_acceso(request, "login_fallido", 429 if espera else 401, intentado)
        if espera:
            raise _demasiados_intentos(espera)
        raise AutenticacionError("Credenciales inválidas")

    limites.login_exitoso(body.username, ip)
    token = token_de_usuario(usuario)
    _auditar_acceso(request, "login", 200, f"Login exitoso: {usuario['username']}", usuario)

    return LoginResponse(
        access_token=token,
        username=usuario["username"],
        nombre_completo=usuario.get("nombre_completo"),
    )


@router.post(
    "/registro",
    response_model=RegistroResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar nuevo usuario",
)
def registro(
    body: RegistroRequest,
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> RegistroResponse:
    """
    Registra un nuevo usuario en el sistema.
    Si ya existe al menos un usuario (el primero es el administrador, lo crea el arranque),
    solo el administrador puede registrar otros (hallazgo #1 de docs/deploy/PLAN.md).
    """
    if listar_usuarios():
        requerir_administrador(request, credentials)
    # Sin filtrar por activo: un usuario desactivado conserva el username (UNIQUE) y antes
    # esto terminaba en un IntegrityError → 500. Mismo contrato que el motor Go: 409.
    from core.database import obtenerUno
    if obtenerUno("SELECT id FROM usuarios WHERE username=?", (body.username,)):
        raise ConflictoError(f"El usuario '{body.username}' ya existe")

    hashed = hash_password(body.password)
    user_id = crear_usuario(
        username=body.username,
        hashed_password=hashed,
        nombre_completo=body.nombre_completo,
        email=body.email,
    )

    registrar_log(
        accion="registro_usuario",
        detalle=f"Nuevo usuario registrado: {body.username}",
    )

    return RegistroResponse(
        id=user_id,
        username=body.username,
        mensaje=f"Usuario '{body.username}' registrado correctamente",
    )


@router.get("/usuarios", response_model=list[UsuarioResponse], summary="Listar usuarios")
def listar_todos_usuarios(
    usuario_actual: dict = Depends(requerir_administrador),
) -> list[UsuarioResponse]:
    """Lista todos los usuarios activos. Solo el administrador."""
    usuarios = listar_usuarios()
    return [
        UsuarioResponse(
            id=u["id"],
            username=u["username"],
            nombre_completo=u.get("nombre_completo"),
            email=u.get("email"),
            creado_en=u["creado_en"],
            ultimo_acceso=u.get("ultimo_acceso"),
        )
        for u in usuarios
    ]


@router.delete(
    "/usuarios/{user_id}",
    response_model=MensajeResponse,
    summary="Desactivar usuario",
)
def eliminar_usuario(
    user_id: int,
    usuario_actual: dict = Depends(requerir_administrador),
) -> MensajeResponse:
    """Desactiva un usuario. Solo el administrador (una API key no puede dejar sin acceso a nadie)."""
    if user_id == usuario_actual["id"]:
        raise ValidacionError("No puedes desactivar tu propio usuario")

    desactivar_usuario(user_id)

    registrar_log(
        accion="desactivar_usuario",
        detalle=f"Usuario {user_id} desactivado por {usuario_actual['username']}",
    )

    return MensajeResponse(mensaje=f"Usuario {user_id} desactivado correctamente")


@router.post("/cambiar-clave", response_model=LoginResponse, summary="Cambiar la propia contraseña")
def cambiar_clave(
    body: CambiarClaveRequest,
    request: Request,
    usuario_actual: dict = Depends(requerir_usuario),
) -> LoginResponse:
    """Cambia la contraseña del usuario del JWT. Los JWT emitidos antes dejan de valer (cierra las
    sesiones abiertas); la respuesta trae uno nuevo. La contraseña actual mal escrita cuenta como
    login fallido."""
    ip = ip_cliente(request)
    espera = limites.login_bloqueado(usuario_actual["username"], ip)
    if espera:
        raise _demasiados_intentos(espera)
    if not verify_password(body.clave_actual, usuario_actual["hashed_password"]):
        espera = limites.login_fallido(usuario_actual["username"], ip)
        if espera:
            raise _demasiados_intentos(espera)
        raise AutenticacionError("La contraseña actual no coincide")
    if body.clave_nueva == body.clave_actual:
        raise ValidacionError("La contraseña nueva debe ser distinta de la actual")
    cambio = cambiar_clave_usuario(usuario_actual["id"], hash_password(body.clave_nueva))
    limites.login_exitoso(usuario_actual["username"], ip)
    return LoginResponse(access_token=token_de_usuario({**usuario_actual, "clave_cambiada_en": cambio}),
                         username=usuario_actual["username"],
                         nombre_completo=usuario_actual.get("nombre_completo"))


@router.get("/me", summary="Obtener información del usuario actual")
def obtener_perfil(
    usuario_actual: dict = Depends(requerir_autenticacion),
) -> dict:
    """Retorna la información del usuario autenticado. Con un token: su id, nombre y rol."""
    if es_token(usuario_actual):
        return {"tipo": "apikey", "id": usuario_actual["api_key_id"],
                "nombre": usuario_actual["nombre"], "rol": usuario_actual["rol"]}
    return {
        "id": usuario_actual["id"],
        "username": usuario_actual["username"],
        "nombre_completo": usuario_actual.get("nombre_completo"),
        "email": usuario_actual.get("email"),
        "creado_en": usuario_actual["creado_en"],
        "ultimo_acceso": usuario_actual.get("ultimo_acceso"),
    }
