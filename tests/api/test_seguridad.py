"""
Fugas de acceso (docs/deploy/PLAN.md, hallazgos #1 y #8), corregidas en la fase 2.
"""
from __future__ import annotations

import pytest

from tests.conftest import CLAVE_CERT, FIXTURES, RUT_EMISOR


def test_rutas_protegidas_exigen_token(cliente):
    assert cliente.get("/api/v1/dte/documentos").status_code == 401


@pytest.mark.hallazgo(1)
def test_registro_sin_token_se_rechaza(cliente):
    r = cliente.post("/api/v1/auth/registro", json={"username": "intruso", "password": "clave-de-prueba-intruso"})
    assert r.status_code in (401, 403)


@pytest.mark.hallazgo(8)
def test_contexto_emision_no_entrega_clave_privada_a_un_jwt(cliente, admin):
    with open(FIXTURES / "cert_demo.p12", "rb") as f:
        r = cliente.post("/api/v1/keystore/certificados", headers=admin,
                         files={"archivo": ("cert_demo.p12", f, "application/x-pkcs12")},
                         data={"password": CLAVE_CERT})
    assert r.status_code in (200, 201), r.text
    cert_id = r.json()["id"]
    r = cliente.post("/api/v1/keystore/negocios", headers=admin, data={"rut": RUT_EMISOR, "cert_id": cert_id})
    assert r.status_code in (200, 201), r.text
    r = cliente.get(f"/api/v1/keystore/negocios/{r.json()['id']}/contexto-emision", headers=admin)
    assert r.status_code in (401, 403) and "key_pem" not in r.text
