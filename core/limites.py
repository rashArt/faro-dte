"""
core/limites.py — Límite de intentos fallidos (docs/deploy/PLAN.md, fase 4).

- **Login:** `settings.login_max_fallos` fallos por usuario+IP bloquean ese par durante
  `settings.login_bloqueo_min` minutos. Mientras dure, ni la contraseña correcta entra.
- **Bearer inválido:** pasados `settings.bearer_max_fallos` por IP en esa misma ventana, los
  inválidos responden 429 en vez de 401. Una credencial válida pasa siempre: un token de 256 bits
  no se adivina, y bloquear la IP cortaría a los demás sistemas que salen por ella (un punto de
  venta con la clave vieja tras renovar no debe dejar sin servicio al resto).

Los contadores viven en la caché de la fase 3 (memoria o Redis). Sus claves llevan un hash, no el
usuario ni la IP en claro.
"""
from __future__ import annotations

import hashlib
import logging

from core.adaptadores.cache import cache
from core.config import settings

logger = logging.getLogger("dte.limites")


def _clave(tipo: str, *partes: str) -> str:
    return f"limite:{tipo}:" + hashlib.sha256("\x1f".join(partes).encode()).hexdigest()[:32]


def _ventana_s() -> int:
    return settings.login_bloqueo_min * 60


def login_bloqueado(usuario: str, ip: str) -> int:
    """Segundos de bloqueo que le quedan al par usuario+IP (0 si puede intentar)."""
    return cache().restante(_clave("login-bloqueo", usuario.lower(), ip))


def login_fallido(usuario: str, ip: str) -> int:
    """Cuenta un fallo. Devuelve los segundos de bloqueo si este fallo lo activó, si no 0."""
    n = cache().incrementar(_clave("login", usuario.lower(), ip), _ventana_s())
    if n < settings.login_max_fallos:
        return 0
    cache().poner(_clave("login-bloqueo", usuario.lower(), ip), "1", _ventana_s())
    cache().borrar(_clave("login", usuario.lower(), ip))
    logger.warning("login bloqueado %d min tras %d fallos (ip %s)", settings.login_bloqueo_min, n, ip)
    return _ventana_s()


def login_exitoso(usuario: str, ip: str) -> None:
    cache().borrar(_clave("login", usuario.lower(), ip))


def bearer_fallido(ip: str) -> int:
    """Cuenta un Bearer inválido. Devuelve los segundos a esperar si la IP pasó el límite, si no 0."""
    clave = _clave("bearer", ip)
    n = cache().incrementar(clave, _ventana_s())
    if n < settings.bearer_max_fallos:
        return 0
    if n == settings.bearer_max_fallos:
        logger.warning("%d Bearer inválidos desde %s en %d min", n, ip, settings.login_bloqueo_min)
    return max(1, cache().restante(clave))
