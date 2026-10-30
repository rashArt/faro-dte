"""
core/adaptadores/cache.py — Caché del proceso, o compartida en Redis si hay `REDIS_URL`.

Solo para lo que ahorra trabajo y no pone en riesgo la consistencia (docs/deploy/ARQUITECTURA.md,
punto 6): hoy, el token del SII. **Nunca folios ni idempotencia**: su verdad está en la base.

Sin `REDIS_URL` todo funciona igual con un diccionario en memoria (una instancia por cliente con
un solo proceso). Con Redis, los valores se guardan **cifrados** con Fernet (`DTE_MASTER_KEY`):
un token del SII es una credencial por una hora y no debe quedar en claro en otro servicio.
"""
from __future__ import annotations

import threading
import time
from typing import Optional, Protocol

from core.config import settings

PREFIJO = "faro:"


class Cache(Protocol):
    def obtener(self, clave: str) -> Optional[str]: ...
    def poner(self, clave: str, valor: str, ttl_s: int) -> None: ...
    def borrar(self, clave: str) -> None: ...


class CacheMemoria:
    """Diccionario con vencimiento, seguro entre hilos (las rutas corren en el pool de FastAPI)."""

    def __init__(self) -> None:
        self._datos: dict[str, tuple[str, float]] = {}
        self._candado = threading.Lock()

    def obtener(self, clave: str) -> Optional[str]:
        with self._candado:
            par = self._datos.get(clave)
            if par is None:
                return None
            if par[1] <= time.monotonic():
                del self._datos[clave]
                return None
            return par[0]

    def poner(self, clave: str, valor: str, ttl_s: int) -> None:
        if ttl_s <= 0:
            return
        with self._candado:
            self._datos[clave] = (valor, time.monotonic() + ttl_s)

    def borrar(self, clave: str) -> None:
        with self._candado:
            self._datos.pop(clave, None)


class CacheRedis:
    """Redis compartido entre procesos o réplicas. Claves con prefijo `faro:`, valores cifrados."""

    def __init__(self, url: str) -> None:
        import redis   # opcional: solo si hay REDIS_URL
        from core.keystore import _fernet
        self._redis = redis.Redis.from_url(url, socket_timeout=2, socket_connect_timeout=2)
        self._fernet = _fernet()

    def obtener(self, clave: str) -> Optional[str]:
        crudo = self._redis.get(PREFIJO + clave)
        return self._fernet.decrypt(crudo).decode() if crudo else None

    def poner(self, clave: str, valor: str, ttl_s: int) -> None:
        if ttl_s > 0:
            self._redis.set(PREFIJO + clave, self._fernet.encrypt(valor.encode()), ex=ttl_s)

    def borrar(self, clave: str) -> None:
        self._redis.delete(PREFIJO + clave)


_cache: Optional[Cache] = None
_candado = threading.Lock()


def cache() -> Cache:
    """La caché del proceso: Redis si `settings.redis_url`, si no memoria."""
    global _cache
    if _cache is None:
        with _candado:
            if _cache is None:
                _cache = CacheRedis(settings.redis_url) if settings.redis_url else CacheMemoria()
    return _cache


def reiniciar(nueva: Optional[Cache] = None) -> None:
    """Descarta la caché actual (pruebas, o al cambiar `REDIS_URL`)."""
    global _cache
    with _candado:
        _cache = nueva
