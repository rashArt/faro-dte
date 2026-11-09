"""
Despacho al SII por documento (docs/deploy/PLAN.md, fase 5). El SII se reemplaza por respuestas
falsas: la suite nunca sale a la red.

- emitir con `enviar` (por defecto) → el despacho envía el sobre guardado y sigue el veredicto.
- El veredicto sale de aceptados/rechazados/reparos del documento, no de `EPR` (L3).
- `enviar: false` → `POST /dte/{id}/enviar` a mano. `ENVIO_SII=desactivado` → 409.
- Envíos fallidos → reintento con espera; al tope, `envio_fallido`. Sin veredicto por horas →
  `sin_veredicto`. Rechazados → folios por anular.
- `emisor` opcional (sale del negocio) y PDF de previsualización marcado BORRADOR.
"""
from __future__ import annotations

import base64
import io
from datetime import datetime, timedelta

import pytest

from tests.conftest import CLAVE_CERT, FIXTURES, RUT_EMISOR

DATOS_NEGOCIO = {"razon_social": "EMPRESA DEMO SPA", "giro": "Cafeteria", "codigo_actividad": 561000,
                 "direccion": "Calle Ficticia 123", "comuna": "Santiago", "ciudad": "Santiago"}


@pytest.fixture
def negocio(instancia, admin):
    """EMPRESA DEMO SPA con el certificado ficticio y sus datos de emisor completos."""
    negocios = instancia.get("/api/v1/keystore/negocios", headers=admin).json()
    negocios = negocios.get("negocios", negocios) if isinstance(negocios, dict) else negocios
    neg = next((n for n in negocios if n["rut"] == RUT_EMISOR), None)
    if neg is None or not neg.get("cert_id"):
        with open(FIXTURES / "cert_demo.p12", "rb") as f:
            r = instancia.post("/api/v1/keystore/certificados", headers=admin,
                               files={"archivo": ("cert_demo.p12", f, "application/x-pkcs12")},
                               data={"password": CLAVE_CERT})
        assert r.status_code == 201, r.text
        if neg is None:
            r = instancia.post("/api/v1/keystore/negocios", headers=admin,
                               data={"rut": RUT_EMISOR, "cert_id": r.json()["id"]})
            assert r.status_code in (200, 201), r.text
            neg = r.json()
    r = instancia.put(f"/api/v1/keystore/negocios/{neg['id']}", headers=admin, json=DATOS_NEGOCIO)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def activo(monkeypatch):
    from core.config import settings
    monkeypatch.setattr(settings, "envio_sii", "activo")


@pytest.fixture
def reloj_libre():
    from core import reloj
    yield reloj
    reloj.fijar(None)


@pytest.fixture
def sii(monkeypatch):
    """SII falso. `respuesta` es lo que devuelve la consulta del veredicto; `falla_envio` hace
    fallar los envíos. Registra cada envío y cada consulta."""
    from core.adaptadores.sii import ClienteBoletas, ClienteFacturas
    import core.seguimiento

    class Falso:
        envios: list = []
        consultas: list = []
        falla_envio = False
        respuesta = {"estado": "EPR", "resuelto": True, "aceptados": 1, "rechazados": 0, "reparos": 0, "glosa": ""}

    def enviar(_canal):
        def _enviar(self, sobre, rut, dv, **_):
            assert sobre.startswith(b"<?xml") and b"Signature" in sobre
            if Falso.falla_envio:
                raise ValueError("El SII rechazó el envío. Estado: 5.")
            Falso.envios.append((_canal, rut, dv))
            return (123456789012345 if _canal == "boleta" else 4242), "ok"
        return _enviar

    def consultar(*_, **__):
        Falso.consultas.append(1)
        return dict(Falso.respuesta)

    Falso.envios, Falso.consultas = [], []
    monkeypatch.setattr(ClienteFacturas, "enviar_dte", enviar("factura"))
    monkeypatch.setattr(ClienteBoletas, "enviar_boletas", enviar("boleta"))
    monkeypatch.setattr(ClienteBoletas, "consultar_estado", consultar)
    monkeypatch.setattr(core.seguimiento, "estado_detallado", consultar)
    return Falso


def _emitir(cliente, cabecera, cuerpo, **extra):
    r = cliente.post("/api/v1/dte/emitir", json={**cuerpo, **extra}, headers=cabecera)
    assert r.status_code == 200, r.text
    return r.json()


def _estado(cliente, cabecera, dte_id, **params):
    r = cliente.get(f"/api/v1/dte/{dte_id}/estado", headers=cabecera, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _mas_tarde(reloj, **delta):
    reloj.fijar((datetime.now() + timedelta(**delta)).isoformat())


def test_ciclo_completo_de_una_factura(instancia, admin, cuerpo, negocio, activo, sii, reloj_libre):
    from core.servicios import envio
    j = _emitir(instancia, admin, cuerpo())
    assert (j["estado"], j["envio"]) == ("generado", "programado")
    assert envio.ciclo() == {"envios": 1, "consultas": 0}
    e = _estado(instancia, admin, j["dte_id"])
    assert (e["estado"], e["track_id"], e["seguimiento"]) == ("enviado", "4242", True)
    assert sii.envios == [("factura", "77777777", "7")]
    assert envio.ciclo()["consultas"] == 0          # la primera consulta espera unos segundos
    _mas_tarde(reloj_libre, minutes=1)
    assert envio.ciclo()["consultas"] == 1
    e = _estado(instancia, admin, j["dte_id"])
    assert e["estado"] == "aceptado" and e["veredicto_en"]
    assert envio.ciclo() == {"envios": 0, "consultas": 0}   # un veredicto final no se consulta más


def test_epr_sin_desglose_no_es_aceptado(instancia, admin, cuerpo, negocio, activo, sii, reloj_libre):
    from core.servicios import envio
    j = _emitir(instancia, admin, cuerpo())
    envio.ciclo()
    sii.respuesta = {"estado": "EPR", "resuelto": False, "glosa": "Envio Procesado"}
    _mas_tarde(reloj_libre, minutes=1)
    envio.ciclo()
    e = _estado(instancia, admin, j["dte_id"])
    assert e["estado"] == "enviado" and e["proxima_accion"]


def test_boleta_rechazada_queda_en_folios_por_anular(instancia, admin, cuerpo, negocio, activo, sii, reloj_libre):
    from core.servicios import envio
    with open(FIXTURES / "caf_39.xml", "rb") as f:
        assert instancia.post("/api/v1/caf", headers=admin, files={"archivo": ("caf_39.xml", f, "text/xml")}).status_code == 200
    j = _emitir(instancia, admin, cuerpo(tipo=39))
    envio.ciclo()
    assert sii.envios == [("boleta", "77777777", "7")]
    assert _estado(instancia, admin, j["dte_id"])["track_id"] == "123456789012345"
    sii.respuesta = {"estado": "EPR", "resuelto": True, "aceptados": 0, "rechazados": 1, "reparos": 0,
                     "detalle": [{"folio": j["folio"], "error": [{"codigo": "BOL-3-001", "descripcion": "demo"}]}]}
    _mas_tarde(reloj_libre, minutes=1)
    envio.ciclo()
    e = _estado(instancia, admin, j["dte_id"])
    assert e["estado"] == "rechazado" and "BOL-3-001" in e["motivo_sii"]
    alertas = instancia.get("/api/v1/dte/alertas", headers=admin).json()
    assert [d["dte_id"] for d in alertas["folios_por_anular"]] == [j["dte_id"]]


def test_reparos_y_reintento_con_el_mismo_id_externo(instancia, admin, cuerpo, negocio, activo, sii, reloj_libre):
    from core.servicios import envio
    a = _emitir(instancia, admin, cuerpo(), id_externo="pedido-501")
    envio.ciclo()
    sii.respuesta = {"estado": "EPR", "resuelto": True, "aceptados": 0, "rechazados": 0, "reparos": 1, "glosa": "Reparo"}
    _mas_tarde(reloj_libre, minutes=1)
    envio.ciclo()
    b = _emitir(instancia, admin, cuerpo(), id_externo="pedido-501")
    assert (b["dte_id"], b["repetido"], b["estado"], b["envio"]) == (a["dte_id"], True, "aceptado_con_reparos",
                                                                      "aceptado_con_reparos")
    assert len(sii.envios) == 1


def test_enviar_false_y_envio_a_mano(instancia, admin, cuerpo, negocio, activo, sii):
    from core.servicios import envio
    j = _emitir(instancia, admin, cuerpo(), enviar=False)
    assert j["envio"] == "no_solicitado"
    assert envio.ciclo() == {"envios": 0, "consultas": 0}
    r = instancia.post(f"/api/v1/dte/{j['dte_id']}/enviar", headers=admin)
    assert r.status_code == 200, r.text
    assert (r.json()["estado"], r.json()["seguimiento"]) == ("enviado", True)
    r = instancia.post(f"/api/v1/dte/{j['dte_id']}/enviar", headers=admin)    # ya enviado: no reenvía
    assert r.status_code == 200 and len(sii.envios) == 1


def test_envio_desactivado(instancia, admin, cuerpo, sii):
    j = _emitir(instancia, admin, cuerpo())
    assert (j["envio"], j["estado"]) == ("desactivado", "generado")
    r = instancia.post(f"/api/v1/dte/{j['dte_id']}/enviar", headers=admin)
    assert r.status_code == 409 and r.json()["error"]["codigo"] == "envio_desactivado"
    assert _estado(instancia, admin, j["dte_id"])["envio_sii"] == "desactivado"
    assert sii.envios == []


def test_envios_fallidos_reintentan_y_al_tope_quedan_envio_fallido(
        instancia, admin, cuerpo, negocio, activo, sii, reloj_libre, monkeypatch):
    from core.config import settings
    from core.servicios import envio
    monkeypatch.setattr(settings, "envio_max_intentos", 2)
    sii.falla_envio = True
    j = _emitir(instancia, admin, cuerpo())
    envio.ciclo()
    e = _estado(instancia, admin, j["dte_id"])
    assert (e["estado"], e["intentos_envio"]) == ("generado", 1) and "Estado: 5" in e["motivo_sii"]
    assert envio.ciclo()["envios"] == 0                          # espera antes de reintentar
    _mas_tarde(reloj_libre, minutes=2)
    envio.ciclo()
    assert _estado(instancia, admin, j["dte_id"])["estado"] == "envio_fallido"
    assert [d["dte_id"] for d in instancia.get("/api/v1/dte/alertas", headers=admin).json()["envio_fallido"]] == [j["dte_id"]]
    sii.falla_envio = False
    r = instancia.post(f"/api/v1/dte/{j['dte_id']}/enviar", headers=admin)   # reintento a mano
    assert r.status_code == 200 and r.json()["estado"] == "enviado"


def test_envio_a_mano_que_falla_responde_502(instancia, admin, cuerpo, negocio, activo, sii):
    sii.falla_envio = True
    j = _emitir(instancia, admin, cuerpo(), enviar=False)
    r = instancia.post(f"/api/v1/dte/{j['dte_id']}/enviar", headers=admin)
    assert r.status_code == 502 and r.json()["error"]["codigo"] == "sii_comunicacion"


def test_sin_veredicto_por_horas_alerta_y_luego_se_resuelve(
        instancia, admin, cuerpo, negocio, activo, sii, reloj_libre):
    from core.servicios import envio
    j = _emitir(instancia, admin, cuerpo())
    envio.ciclo()
    sii.respuesta = {"estado": "SOK", "resuelto": False, "glosa": ""}
    _mas_tarde(reloj_libre, hours=7)
    envio.ciclo()
    assert _estado(instancia, admin, j["dte_id"])["estado"] == "sin_veredicto"
    assert instancia.get("/api/v1/dte/alertas", headers=admin).json()["sin_veredicto"][0]["dte_id"] == j["dte_id"]
    sii.respuesta = {"estado": "EPR", "resuelto": True, "aceptados": 1, "rechazados": 0, "reparos": 0}
    _mas_tarde(reloj_libre, hours=9)
    envio.ciclo()
    assert _estado(instancia, admin, j["dte_id"])["estado"] == "aceptado"


def test_consultar_sii_a_mano(instancia, admin, cuerpo, negocio, activo, sii):
    from core.servicios import envio
    j = _emitir(instancia, admin, cuerpo())
    envio.ciclo()
    assert _estado(instancia, admin, j["dte_id"], consultar_sii="true")["estado"] == "aceptado"
    assert len(sii.consultas) == 1


def test_un_documento_lo_trabaja_un_solo_proceso(instancia, admin, cuerpo, negocio, activo, sii):
    from core.servicios import envio
    j = _emitir(instancia, admin, cuerpo())
    assert envio._tomar(j["dte_id"]) and not envio._tomar(j["dte_id"])
    assert envio.ciclo()["envios"] == 0
    r = instancia.post(f"/api/v1/dte/{j['dte_id']}/enviar", headers=admin)
    assert r.status_code == 409 and r.json()["error"]["codigo"] == "en_curso"
    envio._soltar(j["dte_id"])
    assert envio.ciclo()["envios"] == 1


def test_token_envia_y_consulta_solo_lo_suyo(instancia, admin, cuerpo, negocio, activo, sii):
    r = instancia.post("/api/v1/apikeys", json={"nombre": "punto-de-venta"}, headers=admin)
    token = {"Authorization": f"Bearer {r.json()['api_key']}"}
    propio = _emitir(instancia, token, cuerpo(), enviar=False)["dte_id"]
    ajeno = _emitir(instancia, admin, cuerpo(), enviar=False)["dte_id"]
    assert instancia.post(f"/api/v1/dte/{propio}/enviar", headers=token).status_code == 200
    assert instancia.post(f"/api/v1/dte/{ajeno}/enviar", headers=token).status_code == 404
    assert instancia.get(f"/api/v1/dte/{ajeno}/estado", headers=token).status_code == 404
    assert instancia.get("/api/v1/dte/alertas", headers=token).status_code == 403


# ---- Emisor opcional y BORRADOR ----

def _sin_emisor(c: dict) -> dict:
    c = {**c, "dte": dict(c["dte"])}
    del c["dte"]["emisor"], c["certificado_p12_b64"], c["password_certificado"]
    return c


def test_emitir_sin_emisor_usa_el_negocio(instancia, admin, cuerpo, negocio):
    j = _emitir(instancia, admin, _sin_emisor(cuerpo()), id_externo="pedido-sin-emisor")
    assert j["estado"] == "generado"
    xml = base64.b64decode(j["xml_envio_b64"]).decode("ISO-8859-1")
    assert "<RznSoc>EMPRESA DEMO SPA</RznSoc>" in xml and "<GiroEmis>Cafeteria</GiroEmis>" in xml
    instancia.put(f"/api/v1/keystore/negocios/{negocio['id']}", headers=admin, json={"giro": "Otro giro"})
    b = _emitir(instancia, admin, _sin_emisor(cuerpo()), id_externo="pedido-sin-emisor")
    assert (b["dte_id"], b["repetido"]) == (j["dte_id"], True)     # mismo JSON: reintento, no corrección


def test_emitir_sin_emisor_con_datos_incompletos_responde_422(instancia, admin, cuerpo, negocio):
    instancia.put(f"/api/v1/keystore/negocios/{negocio['id']}", headers=admin, json={"giro": ""})
    r = instancia.post("/api/v1/dte/emitir", json=_sin_emisor(cuerpo()), headers=admin)
    assert r.status_code == 422 and r.json()["error"]["detalle"]["faltan"] == ["giro"]


def test_previsualizar_sin_emisor_ni_cert_sale_borrador(instancia, admin, cuerpo, negocio):
    from pypdf import PdfReader
    r = instancia.post("/api/v1/dte/previsualizar", json={"dte": _sin_emisor(cuerpo())["dte"]}, headers=admin)
    assert r.status_code == 200, r.text
    assert r.json()["valido_xsd"] is True
    texto = PdfReader(io.BytesIO(base64.b64decode(r.json()["pdf_b64"]))).pages[0].extract_text()
    assert "BORRADOR" in texto
