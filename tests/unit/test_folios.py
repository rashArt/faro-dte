"""
Folio atómico bajo concurrencia (L7). Es el gate de la fase 1: al cambiar la capa de datos
(SQLAlchemy, PostgreSQL) esta prueba tiene que seguir en verde en las dos bases.
"""
from __future__ import annotations

import threading

from tests.conftest import RUT_EMISOR, folio_siguiente

HILOS = 50


def test_50_hilos_sobre_el_mismo_caf_dan_folios_unicos_y_consecutivos(instancia):
    from core.models import consumir_siguiente_folio
    folios: list[int] = []
    errores: list[BaseException] = []
    candado = threading.Lock()

    def tomar():
        try:
            f = consumir_siguiente_folio(RUT_EMISOR, 33)
            with candado:
                folios.append(f)
        except BaseException as e:  # noqa: BLE001 — se reporta abajo
            errores.append(e)

    hilos = [threading.Thread(target=tomar) for _ in range(HILOS)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert not errores, errores[:3]
    assert sorted(folios) == list(range(1, HILOS + 1))
    assert folio_siguiente() == HILOS + 1
