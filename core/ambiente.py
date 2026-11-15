"""
core/ambiente.py — El ambiente del SII en que trabaja la instancia.

Cada CAF, documento, emisión y RVD guarda el ambiente en que nació (columna `ambiente`, llave
foránea a la tabla `ambientes`, migración `0005`). El folio 1 de certificación y el folio 1 de
producción son documentos distintos: toda consulta por folio, CAF o `id_externo` filtra por
ambiente.

`ambiente_actual()` es el ÚNICO lugar que decide en qué ambiente se trabaja ahora: el de la
empresa de la instancia (`negocios.ambiente`), que se cambia por la API (`PUT
/api/v1/empresa/ambiente`, core/empresa.py). `SII_AMBIENTE` es solo el valor con que nace una
empresa nueva, y el que rige mientras la instancia no tiene empresa.

Una empresa puede estar en `simulacion` (el modo demo): emite con el mismo motor, pero nada sale
al SII ni se le consulta nada, ni siquiera su RUT. Antes de tocar al SII pregunta `usa_sii()`.
"""
from __future__ import annotations

from typing import Optional

from core.config import settings

CERTIFICACION = "certificacion"   # maullin / pangal: pruebas del SII, sin efecto tributario
PRODUCCION = "produccion"         # palena / rahue: documentos reales
SIMULACION = "simulacion"         # modo demo: nunca sale al SII ni lo consulta

# Las filas de la tabla `ambientes` (migración 0005).
AMBIENTES = {
    CERTIFICACION: "Certificación (pruebas del SII)",
    PRODUCCION: "Producción (documentos reales)",
    SIMULACION: "Simulación (demo local, no sale al SII)",
}


def ambiente_inicial() -> str:
    """El ambiente con que nace una empresa nueva (`SII_AMBIENTE`)."""
    return settings.sii_ambiente if settings.sii_ambiente in AMBIENTES else CERTIFICACION


def usa_sii(ambiente: Optional[str]) -> bool:
    """¿En este ambiente se habla con el SII? En `simulacion` (demo) nunca."""
    return ambiente in (CERTIFICACION, PRODUCCION)


def ambiente_actual() -> str:
    """El ambiente en que la instancia emite ahora: el de su empresa."""
    from core import database
    try:
        fila = database.obtenerUno("SELECT ambiente FROM negocios ORDER BY id LIMIT 1")
    except Exception:  # noqa: BLE001 — base sin migrar todavía (arranque)
        fila = None
    if fila and fila[0] in AMBIENTES:
        return fila[0]
    return ambiente_inicial()


def resolver(ambiente: Optional[str] = None) -> str:
    """`ambiente` si viene; si no, el actual. Para funciones con el ambiente opcional."""
    return ambiente or ambiente_actual()
