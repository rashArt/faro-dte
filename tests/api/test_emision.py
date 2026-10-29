"""
Emisión por la API: folios, reintentos y validación (docs/deploy/PLAN.md, hallazgos #3 a #7 y #15).

Las pruebas marcadas `xfail(strict=True)` reproducen un hallazgo abierto: hoy fallan a
propósito. Cuando la fase 2 lo corrija, pasan, pytest lo reporta como XPASS y la suite se
pone roja hasta quitar la marca. Así ningún hallazgo se cierra sin que alguien lo note.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tests.conftest import TMP, folio_siguiente


def test_emision_basica_asigna_folio_y_guarda(instancia, admin, cuerpo):
    r = instancia.post("/api/v1/dte/emitir", json=cuerpo(), headers=admin)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["folio"] == 1
    assert folio_siguiente() == 2
    assert instancia.get(f"/api/v1/db/dtes/{j['dte_id']}", headers=admin).status_code == 200


def test_folios_automaticos_son_consecutivos(instancia, admin, cuerpo):
    folios = [instancia.post("/api/v1/dte/emitir", json=cuerpo(f"Venta {i}"), headers=admin).json()["folio"]
              for i in range(5)]
    assert folios == [1, 2, 3, 4, 5]


@pytest.mark.hallazgo(3)
@pytest.mark.xfail(strict=True, reason="#3: sin id_externo un reintento crea otro documento (fase 2)")
def test_reintento_con_mismo_id_externo_devuelve_mismo_folio(instancia, admin, cuerpo):
    c = cuerpo("Factura pedido 123")
    c["id_externo"] = "pedido-123"
    a = instancia.post("/api/v1/dte/emitir", json=c, headers=admin).json()
    b = instancia.post("/api/v1/dte/emitir", json=c, headers=admin).json()
    assert (a["dte_id"], a["folio"]) == (b["dte_id"], b["folio"])
    assert folio_siguiente() == 2


@pytest.mark.hallazgo(4)
@pytest.mark.xfail(strict=True, reason="#4/#7: folio explícito repetido da 500, retrocede el contador y pisa el PDF (fase 2)")
def test_folio_explicito_repetido_no_retrocede_ni_sobrescribe(instancia, admin, cuerpo):
    for i in range(3):
        instancia.post("/api/v1/dte/emitir", json=cuerpo(f"Venta {i}"), headers=admin)
    pdf = Path(TMP / "storage").parent / instancia.get("/api/v1/db/dtes", headers=admin).json()["dtes"][-1]["pdf_path"]
    huella = hashlib.sha256(pdf.read_bytes()).hexdigest() if pdf.exists() else None
    r = instancia.post("/api/v1/dte/emitir", json=cuerpo("Repite el folio 1", folio=1), headers=admin)
    assert r.status_code == 409
    assert folio_siguiente() == 4
    if huella:
        assert hashlib.sha256(pdf.read_bytes()).hexdigest() == huella
    assert instancia.post("/api/v1/dte/emitir", json=cuerpo("Sigue"), headers=admin).status_code == 200


@pytest.mark.hallazgo(5)
@pytest.mark.xfail(strict=True, reason="#5: las facturas no se validan contra el XSD antes de gastar folio (fase 2)")
def test_giro_receptor_largo_responde_422_sin_gastar_folio(instancia, admin, cuerpo):
    c = cuerpo()
    c["dte"]["receptor"]["giro"] = "G" * 80
    r = instancia.post("/api/v1/dte/emitir", json=c, headers=admin)
    assert r.status_code == 422
    assert folio_siguiente() == 1


@pytest.mark.hallazgo(15)
@pytest.mark.parametrize("tipo", [56, 61])
@pytest.mark.xfail(strict=True, reason="#15: notas de débito y crédito sin referencias se emiten igual (fase 2)")
def test_nota_sin_referencias_responde_422(instancia, admin, cuerpo, tipo):
    r = instancia.post("/api/v1/dte/emitir", json=cuerpo(tipo=tipo), headers=admin)
    assert r.status_code == 422
    assert folio_siguiente(tipo) == 1
