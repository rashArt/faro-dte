"""
Política async (docs/deploy/PLAN.md, fase 3, hallazgo #12): las rutas que firman, escriben disco
o hablan con la base corren en el pool de hilos de FastAPI, así que el event loop sigue libre.

Antes de la fase 3, con 8 emisiones en paralelo `/health` pasaba de 19 ms a 1.533 ms: cada
emisión bloqueaba el loop entero mientras firmaba.
"""
from __future__ import annotations

import threading
import time

from tests.conftest import folio_siguiente

EMISIONES = 8
TOPE_HEALTH_S = 1.0   # medido: ~0,35 s con hilos (GIL) contra ~2,5 s antes de la fase 3


def test_health_responde_rapido_mientras_se_emite_en_paralelo(instancia, admin, cuerpo):
    instancia.get("/health")   # calienta imports
    respuestas: list = []
    hilos = [threading.Thread(target=lambda i=i: respuestas.append(
        instancia.post("/api/v1/dte/emitir", json=cuerpo(f"Paralela {i}"), headers=admin).status_code))
        for i in range(EMISIONES)]
    for h in hilos:
        h.start()
    tiempos = []
    while any(h.is_alive() for h in hilos):
        inicio = time.perf_counter()
        assert instancia.get("/health").status_code == 200
        tiempos.append(time.perf_counter() - inicio)
        time.sleep(0.02)
    for h in hilos:
        h.join()
    assert respuestas == [200] * EMISIONES
    assert folio_siguiente() == EMISIONES + 1
    assert tiempos and max(tiempos) < TOPE_HEALTH_S, f"/health tardó hasta {max(tiempos) * 1000:.0f} ms"
