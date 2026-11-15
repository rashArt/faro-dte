"""
Ambientes, empresa y selección de CAF (docs/deploy/PLAN.md, fase 7; migración `0005`).

- Una instancia, una empresa: `/api/v1/empresa` sin id ni RUT; otra empresa responde 409.
- El ambiente es de la empresa y se cambia por la API, con requisitos y auditoría.
- Certificación y producción numeran por separado: el folio 1 y el mismo `id_externo` existen
  en los dos y son documentos distintos. Los CAF de un ambiente no se usan en el otro.
- Con varios CAF del mismo tipo se usa el vigente que vence primero; al agotarse o vencer se
  pasa solo al siguiente; el administrador puede fijar otro. Nunca se usa un CAF vencido.
"""
from __future__ import annotations

import io

import pytest

from tests.conftest import CLAVE_CERT, FIXTURES, RUT_EMISOR

DATOS = {"giro": "Cafeteria", "codigo_actividad": 561000, "direccion": "Calle Ficticia 123",
         "comuna": "Santiago", "ciudad": "Santiago"}


@pytest.fixture
def sin_empresa(instancia, monkeypatch):
    """La instancia sin empresa ni certificados, antes y después de la prueba. Las pruebas usan el
    RUT de demostración en todos los ambientes (con el SII simulado): se quita su bloqueo."""
    import core.empresa
    from core.database import get_db
    monkeypatch.setattr(core.empresa, "RUTS_SOLO_DEMO", frozenset())

    def limpiar():
        with get_db() as con:
            con.execute("DELETE FROM negocios")
            con.execute("DELETE FROM certificados")
    limpiar()
    yield instancia
    limpiar()


@pytest.fixture
def empresa(sin_empresa, admin):
    """La empresa demo creada por la API, con sus datos y su certificado. Nace en certificación."""
    r = sin_empresa.put("/api/v1/empresa", headers=admin, json={"rut": RUT_EMISOR, **DATOS})
    assert r.status_code == 200, r.text
    with open(FIXTURES / "cert_demo.p12", "rb") as f:
        r = sin_empresa.put("/api/v1/empresa/certificado", headers=admin, data={"password": CLAVE_CERT},
                            files={"archivo": ("cert.p12", f, "application/x-pkcs12")})
    assert r.status_code == 200, r.text
    return sin_empresa


@pytest.fixture
def segura(monkeypatch):
    """Configuración sin avisos de seguridad, tenga lo que tenga el `.env` local."""
    from core.config import settings
    monkeypatch.setattr(settings, "cors_origins", "https://sin-navegador.example.com")


def _emitir(cliente, cabecera, cuerpo, id_externo):
    return cliente.post("/api/v1/dte/emitir", json={**cuerpo, "id_externo": id_externo}, headers=cabecera)


def _cargar_caf(cliente, cabecera, tipo: int = 33, ambiente: str | None = None, xml: bytes | None = None):
    contenido = xml if xml is not None else (FIXTURES / f"caf_{tipo}.xml").read_bytes()
    return cliente.post("/api/v1/caf", headers=cabecera, data={"ambiente": ambiente} if ambiente else None,
                        files={"archivo": ("caf.xml", io.BytesIO(contenido), "text/xml")})


def _caf_nuevo(desde: int, hasta: int, tipo: int = 33) -> bytes:
    """Otro CAF ficticio del mismo emisor, con otro rango (L14)."""
    from core.simulacion import _caf_ficticio
    return _caf_ficticio(tipo, desde, hasta).encode("ISO-8859-1")


def _cafs(tipo: int = 33) -> list[tuple]:
    from core.database import obtener_todos
    return [tuple(f) for f in obtener_todos(
        "SELECT ambiente, folio_desde, folio_siguiente, en_uso FROM cafs WHERE rut_emisor=? AND tipo_dte=? "
        "ORDER BY ambiente, folio_desde", (RUT_EMISOR, tipo))]


def _fecha_caf(desde: int, fecha: str) -> None:
    from core.database import ejecutar
    ejecutar("UPDATE cafs SET fecha_autorizacion=? WHERE rut_emisor=? AND tipo_dte=33 AND folio_desde=?",
             (fecha, RUT_EMISOR, desde))


def _pasar(cliente, admin, ambiente: str):
    return cliente.put("/api/v1/empresa/ambiente", headers=admin,
                       json={"ambiente": ambiente, "confirmar_rut": RUT_EMISOR})


# ---------------------------------------------------------------------------------------------
# Empresa
# ---------------------------------------------------------------------------------------------
def test_empresa_se_crea_sin_id_y_nace_en_el_ambiente_inicial(sin_empresa, admin):
    r = sin_empresa.get("/api/v1/empresa", headers=admin).json()
    assert (r["configurada"], r["ambiente"]) == (False, "certificacion")
    assert sin_empresa.put("/api/v1/empresa", headers=admin, json=DATOS).status_code == 422   # falta el RUT

    r = sin_empresa.put("/api/v1/empresa", headers=admin, json={"rut": RUT_EMISOR, **DATOS})
    assert r.status_code == 200, r.text
    e = r.json()
    assert (e["configurada"], e["rut"], e["razon_social"], e["ambiente"]) == (True, RUT_EMISOR, "EMPRESA DEMO SPA",
                                                                             "certificacion")
    assert e["certificado"] is None and e["produccion"]["puede"] is False

    r = sin_empresa.put("/api/v1/empresa", headers=admin, json={"giro": "Otro giro"})   # actualizar: sin RUT
    assert r.json()["giro"] == "Otro giro" and r.json()["direccion"] == DATOS["direccion"]


def test_una_instancia_no_admite_otra_empresa(empresa, admin):
    r = empresa.put("/api/v1/empresa", headers=admin, json={"rut": "76111111-6"})
    assert r.status_code == 409 and r.json()["error"]["codigo"] == "empresa_unica"
    r = empresa.post("/api/v1/keystore/negocios", headers=admin, data={"rut": "76111111-6"})   # ruta heredada
    assert r.status_code == 409 and r.json()["error"]["codigo"] == "empresa_unica"


def test_la_ruta_heredada_no_cambia_el_ambiente(empresa, admin):
    r = empresa.post("/api/v1/keystore/negocios", headers=admin, data={"rut": RUT_EMISOR, "ambiente": "produccion"})
    assert r.status_code == 201, r.text
    e = empresa.get("/api/v1/empresa", headers=admin).json()
    assert e["ambiente"] == "certificacion" and e["certificado"] is not None   # tampoco suelta el certificado


def test_el_certificado_queda_cifrado_y_sin_secretos_en_la_respuesta(empresa, admin):
    cert = empresa.get("/api/v1/empresa", headers=admin).json()["certificado"]
    assert cert["vigente"] is True and set(cert) == {"id", "rut", "nombre", "vencimiento", "creado", "vigente"}


def test_emitir_sin_emisor_usa_la_empresa(empresa, admin, cuerpo):
    c = cuerpo()
    del c["dte"]["emisor"], c["certificado_p12_b64"], c["password_certificado"]
    r = _emitir(empresa, admin, c, "pedido-710")
    assert r.status_code == 200, r.text
    assert r.json()["folio"] == 1


def test_un_token_lee_la_empresa_pero_no_la_cambia(empresa, admin):
    clave = empresa.post("/api/v1/apikeys", json={"nombre": "caja"}, headers=admin).json()
    token = {"Authorization": f"Bearer {clave.get('api_key') or clave.get('key') or clave.get('token')}"}
    assert empresa.get("/api/v1/empresa", headers=token).status_code == 200
    assert empresa.get("/api/v1/empresa/folios", headers=token).status_code == 200
    assert empresa.put("/api/v1/empresa", headers=token, json={"giro": "x"}).status_code == 403
    assert _pasar(empresa, token, "produccion").status_code == 403
    caf_id = empresa.get("/api/v1/caf", headers=admin).json()[0]["id"]
    assert empresa.put(f"/api/v1/caf/{caf_id}/en-uso", headers=token).status_code == 403


# ---------------------------------------------------------------------------------------------
# Cambio de ambiente
# ---------------------------------------------------------------------------------------------
def test_pasar_a_produccion_exige_los_requisitos(empresa, admin, monkeypatch):
    from core.config import settings
    import core.negocios
    monkeypatch.setattr(settings, "cors_origins", "*")
    r = _pasar(empresa, admin, "produccion")
    assert r.status_code == 409 and r.json()["error"]["codigo"] == "requisitos_produccion"
    codigos = {f["codigo"] for f in r.json()["error"]["detalle"]["falta"]}
    assert codigos == {"caf_produccion", "seguridad"}     # CORS abierto y sin CAF de producción
    assert empresa.get("/api/v1/empresa", headers=admin).json()["ambiente"] == "certificacion"

    # Con todo lo local en regla, falta que el SII la tenga autorizada en producción.
    monkeypatch.setattr(settings, "cors_origins", "https://sin-navegador.example.com")
    assert _cargar_caf(empresa, admin, ambiente="produccion").status_code in (200, 201)
    monkeypatch.setattr(core.negocios, "info_empresa", lambda rut, *a, **k: None)
    r = _pasar(empresa, admin, "produccion")
    assert [f["codigo"] for f in r.json()["error"]["detalle"]["falta"]] == ["autorizacion_sii"]

    def sin_respuesta(*a, **k):
        raise OSError("el SII no responde")
    monkeypatch.setattr(core.negocios, "info_empresa", sin_respuesta)
    assert _pasar(empresa, admin, "produccion").status_code == 409   # sin respuesta no se asume nada


def test_el_cambio_pide_repetir_el_rut(empresa, admin, segura):
    _cargar_caf(empresa, admin, ambiente="produccion")
    r = empresa.put("/api/v1/empresa/ambiente", headers=admin,
                    json={"ambiente": "produccion", "confirmar_rut": "76111111-6"})
    assert r.status_code == 422
    assert empresa.get("/api/v1/empresa", headers=admin).json()["ambiente"] == "certificacion"
    r = empresa.put("/api/v1/empresa/ambiente", headers=admin,
                    json={"ambiente": "pruebas", "confirmar_rut": RUT_EMISOR})
    assert r.status_code == 422


def test_mismo_folio_y_mismo_id_externo_en_los_dos_ambientes(empresa, admin, cuerpo, segura):
    a = _emitir(empresa, admin, cuerpo(), "pedido-701")
    assert a.status_code == 200, a.text
    assert (a.json()["folio"], a.json()["ambiente"]) == (1, "certificacion")

    # El CAF de producción se carga ANTES del cambio y no toca el de certificación.
    assert _cargar_caf(empresa, admin, ambiente="produccion").status_code in (200, 201)
    assert _cafs() == [("certificacion", 1, 2, 1), ("produccion", 1, 1, 0)]

    r = _pasar(empresa, admin, "produccion")
    assert r.status_code == 200, r.text
    assert (r.json()["cambiado"], r.json()["anterior"], r.json()["empresa"]["ambiente"]) == (True, "certificacion",
                                                                                           "produccion")
    b = _emitir(empresa, admin, cuerpo(), "pedido-701")
    assert b.status_code == 200, b.text
    assert (b.json()["folio"], b.json()["ambiente"], b.json()["repetido"]) == (1, "produccion", False)
    assert b.json()["dte_id"] != a.json()["dte_id"]

    # Buscar por folio devuelve el documento del ambiente actual.
    ruta = f"/api/v1/dte/documentos/buscar?rut_emisor={RUT_EMISOR}&tipo_dte=33&folio=1"
    assert empresa.get(ruta, headers=admin).json()["id"] == b.json()["dte_id"]

    # Volver a certificación no pide requisitos; cada ambiente recuerda su venta.
    assert _pasar(empresa, admin, "certificacion").status_code == 200
    c = _emitir(empresa, admin, cuerpo(), "pedido-701").json()
    assert (c["dte_id"], c["repetido"]) == (a.json()["dte_id"], True)
    assert empresa.get(ruta, headers=admin).json()["id"] == a.json()["dte_id"]
    assert _cafs() == [("certificacion", 1, 2, 1), ("produccion", 1, 2, 1)]

    # Queda en la auditoría quién cambió y de qué ambiente a cuál.
    from core.database import obtener_todos
    cambios = [tuple(f) for f in obtener_todos(
        "SELECT actor, detalle FROM audit_log WHERE accion='cambio_ambiente' ORDER BY id")]
    assert [c[1] for c in cambios] == ['{"de": "certificacion", "a": "produccion"}',
                                      '{"de": "produccion", "a": "certificacion"}']
    assert all(c[0].startswith("usuario:") for c in cambios)


def test_en_produccion_no_se_usa_el_caf_de_certificacion(empresa, admin, cuerpo):
    from core.database import ejecutar
    ejecutar("UPDATE negocios SET ambiente='produccion'")   # sin CAF de producción (no pasa por la API)
    r = _emitir(empresa, admin, cuerpo(), "pedido-700")
    assert r.status_code == 409, r.text
    assert r.json()["error"]["detalle"]["motivo"] == "sin_caf"
    assert _cafs() == [("certificacion", 1, 1, 0)]          # no se gastó ningún folio


def test_un_documento_se_envia_en_el_ambiente_en_que_nacio(empresa):
    """El cliente del SII se arma con el ambiente del documento, no con el de la instancia."""
    import core.adaptadores.sii as adaptadores
    from core.database import ejecutar
    from core.errors import ConflictoError
    from core.servicios import envio

    vistos = []

    class _Cliente:
        def __init__(self, cert, ambiente):
            vistos.append(ambiente.value)

    ejecutar("UPDATE negocios SET ambiente='produccion'")
    doc = {"id": 1, "tipo_dte": 33, "rut_emisor": RUT_EMISOR, "ambiente": "certificacion"}
    original = (adaptadores.ClienteFacturas, envio._certificado)
    adaptadores.ClienteFacturas, envio._certificado = _Cliente, lambda d: None
    try:
        envio._cliente(doc)
        with pytest.raises(ConflictoError):
            envio._cliente({**doc, "ambiente": "simulacion"})
    finally:
        adaptadores.ClienteFacturas, envio._certificado = original
    assert vistos == ["certificacion"]


# ---------------------------------------------------------------------------------------------
# Qué CAF se usa
# ---------------------------------------------------------------------------------------------
def test_cargar_otro_caf_no_apaga_el_anterior_y_se_usa_el_que_vence_primero(instancia, admin, cuerpo):
    assert _cargar_caf(instancia, admin, xml=_caf_nuevo(101, 103)).status_code in (200, 201)
    _fecha_caf(1, "2026-08-01")      # el del rango 1… es el más antiguo: vence primero
    assert _emitir(instancia, admin, cuerpo(), "pedido-720").json()["folio"] == 1
    assert _cafs() == [("certificacion", 1, 2, 1), ("certificacion", 101, 101, 0)]


def test_al_agotarse_un_caf_se_pasa_solo_al_siguiente(instancia, admin, cuerpo):
    from core.database import ejecutar
    _cargar_caf(instancia, admin, xml=_caf_nuevo(101, 103))
    _fecha_caf(1, "2026-08-01")
    assert _emitir(instancia, admin, cuerpo(), "pedido-721").json()["folio"] == 1
    ejecutar("UPDATE cafs SET folio_siguiente = folio_hasta + 1 WHERE rut_emisor=? AND tipo_dte=33 AND folio_desde=1",
             (RUT_EMISOR,))           # el primero se agotó
    r = _emitir(instancia, admin, cuerpo(), "pedido-722")
    assert r.status_code == 200, r.text
    assert r.json()["folio"] == 101
    assert [c[3] for c in _cafs()] == [0, 1]


def test_un_caf_vencido_no_se_usa(instancia, admin, cuerpo):
    _fecha_caf(1, "2026-01-01")      # más de 6 meses
    r = _emitir(instancia, admin, cuerpo(), "pedido-723")
    assert r.status_code == 409, r.text
    assert r.json()["error"]["detalle"]["motivo"] == "vencido"
    assert _cafs() == [("certificacion", 1, 1, 0)]     # el folio no se gastó

    # Con otro CAF vigente, se emite con ese.
    _cargar_caf(instancia, admin, xml=_caf_nuevo(101, 103))
    assert _emitir(instancia, admin, cuerpo(), "pedido-723").json()["folio"] == 101
    folios = instancia.get("/api/v1/caf", headers=admin).json()
    assert {(c["folio_desde"], c["vigente"], c["en_uso"]) for c in folios if c["tipo_dte"] == 33} \
        == {(1, False, False), (101, True, True)}


def test_no_se_carga_un_caf_ya_vencido(instancia, admin):
    import re
    xml = re.sub(rb"<FA>[^<]*</FA>", b"<FA>2020-01-01</FA>", _caf_nuevo(201, 210))
    r = _cargar_caf(instancia, admin, xml=xml)
    assert r.status_code == 422, r.text
    assert "venció" in r.json()["error"]["mensaje"]
    assert _cargar_caf(instancia, admin, xml=b"<CAF>roto").status_code == 422   # XML mal formado: 422, no 500


def test_el_administrador_fija_el_caf_en_uso(instancia, admin, cuerpo):
    nuevo = _cargar_caf(instancia, admin, xml=_caf_nuevo(101, 103)).json()
    _fecha_caf(1, "2026-08-01")
    assert _emitir(instancia, admin, cuerpo(), "pedido-724").json()["folio"] == 1    # el motor eligió el antiguo
    r = instancia.put(f"/api/v1/caf/{nuevo['id']}/en-uso", headers=admin)
    assert r.status_code == 200 and r.json()["en_uso"] is True
    assert _emitir(instancia, admin, cuerpo(), "pedido-725").json()["folio"] == 101

    _fecha_caf(1, "2026-01-01")      # el antiguo venció: ya no se puede fijar
    viejo = next(c["id"] for c in instancia.get("/api/v1/caf", headers=admin).json() if c["folio_desde"] == 1
                 and c["tipo_dte"] == 33)
    assert instancia.put(f"/api/v1/caf/{viejo}/en-uso", headers=admin).status_code == 409
    assert instancia.put("/api/v1/caf/999999/en-uso", headers=admin).status_code == 404


def test_folios_de_la_empresa_por_tipo(empresa, admin):
    _cargar_caf(empresa, admin, xml=_caf_nuevo(101, 103))
    r = empresa.get("/api/v1/empresa/folios", headers=admin).json()
    assert (r["rut"], r["ambiente"]) == (RUT_EMISOR, "certificacion")
    t33 = next(t for t in r["tipos"] if t["tipo_dte"] == 33)
    assert len(t33["cafs"]) == 2 and t33["disponibles"] == sum(c["restantes"] for c in t33["cafs"])
    assert all(c["vence"] for c in t33["cafs"])
    assert empresa.get("/api/v1/empresa/folios?ambiente=produccion", headers=admin).json()["tipos"] == []


# ---------------------------------------------------------------------------------------------
# Modo demo (`simulacion`): la API se aprende sin tocar al SII
# ---------------------------------------------------------------------------------------------
@pytest.fixture
def sii_prohibido(monkeypatch):
    """La instancia nace en demo y cualquier consulta al registro del SII hace fallar la prueba."""
    import core.negocios
    from core.config import settings

    def prohibido(*a, **k):
        raise AssertionError("en modo demo no se consulta al SII")
    monkeypatch.setattr(settings, "sii_ambiente", "simulacion")
    monkeypatch.setattr(core.negocios, "info_empresa", prohibido)


@pytest.fixture
def demo(sin_empresa, admin, sii_prohibido):
    """La empresa creada en modo demo, con sus datos, su certificado y un CAF ficticios."""
    r = sin_empresa.put("/api/v1/empresa", headers=admin,
                        json={"rut": RUT_EMISOR, "razon_social": "EMPRESA DEMO SPA", **DATOS})
    assert r.status_code == 200, r.text
    with open(FIXTURES / "cert_demo.p12", "rb") as f:
        r = sin_empresa.put("/api/v1/empresa/certificado", headers=admin, data={"password": CLAVE_CERT},
                            files={"archivo": ("cert.p12", f, "application/x-pkcs12")})
    assert r.status_code == 200, r.text
    return sin_empresa


def test_en_demo_la_empresa_se_crea_sin_consultar_al_sii(sin_empresa, admin, sii_prohibido):
    assert sin_empresa.get("/api/v1/empresa", headers=admin).json()["ambiente"] == "simulacion"
    r = sin_empresa.put("/api/v1/empresa", headers=admin, json={"rut": "77777777-1", **DATOS})
    assert r.status_code == 422, r.text                      # dígito verificador equivocado
    r = sin_empresa.put("/api/v1/empresa", headers=admin,
                        json={"rut": "77.777.777-7", "razon_social": "EMPRESA DEMO SPA", **DATOS})
    assert r.status_code == 200, r.text
    e = r.json()
    assert (e["rut"], e["razon_social"], e["ambiente"]) == (RUT_EMISOR, "EMPRESA DEMO SPA", "simulacion")


def test_en_demo_se_emite_y_nada_sale_al_sii(demo, admin, cuerpo, monkeypatch):
    from core.config import settings
    monkeypatch.setattr(settings, "envio_sii", "activo")     # ni con el envío activo
    r = _cargar_caf(demo, admin)                             # sin `ambiente`: el de la empresa
    assert r.status_code in (200, 201) and r.json()["ambiente"] == "simulacion", r.text
    c = cuerpo()
    del c["dte"]["emisor"], c["certificado_p12_b64"], c["password_certificado"]
    r = _emitir(demo, admin, c, "pedido-730")
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["folio"], d["ambiente"], d["envio"], d["estado"]) == (1, "simulacion", "simulacion", "generado")
    r = demo.post(f"/api/v1/dte/{d['dte_id']}/enviar", headers=admin)
    assert r.status_code == 409 and r.json()["error"]["codigo"] == "ambiente_sin_sii"
    assert ("certificacion", 1, 1, 0) in _cafs()             # el CAF de certificación no se tocó


def test_salir_de_demo_valida_el_rut_en_el_sii(sin_empresa, admin, sii_prohibido, monkeypatch):
    import core.negocios
    assert sin_empresa.put("/api/v1/empresa", headers=admin, json={"rut": RUT_EMISOR, **DATOS}).status_code == 200
    assert sin_empresa.get("/api/v1/empresa", headers=admin).json()["razon_social"] is None

    monkeypatch.setattr(core.negocios, "info_empresa", lambda rut, *a, **k: None)   # el SII no lo conoce
    r = _pasar(sin_empresa, admin, "certificacion")
    assert r.status_code == 409 and r.json()["error"]["codigo"] == "requisitos_certificacion"
    assert [f["codigo"] for f in r.json()["error"]["detalle"]["falta"]] == ["rut_sii"]

    def sin_respuesta(*a, **k):
        raise OSError("el SII no responde")
    monkeypatch.setattr(core.negocios, "info_empresa", sin_respuesta)
    assert _pasar(sin_empresa, admin, "certificacion").status_code == 409            # sin respuesta no se asume nada
    assert sin_empresa.get("/api/v1/empresa", headers=admin).json()["ambiente"] == "simulacion"

    visto = []

    def ficha(rut, ambiente="certificacion", *a, **k):
        visto.append(ambiente)
        return {"rut": rut, "razon_social": "EMPRESA DEMO SPA", "autorizado": True, "tipos": [],
                "resolucion": {"numero": 0, "fecha": ""}}
    monkeypatch.setattr(core.negocios, "info_empresa", ficha)
    r = _pasar(sin_empresa, admin, "certificacion")
    assert r.status_code == 200, r.text
    e = r.json()["empresa"]
    assert (e["ambiente"], e["razon_social"]) == ("certificacion", "EMPRESA DEMO SPA")   # la del SII
    assert visto[0] == "certificacion"

    monkeypatch.setattr(core.negocios, "info_empresa", sin_respuesta)                # volver a demo no pregunta
    r = _pasar(sin_empresa, admin, "simulacion")
    assert r.status_code == 200 and r.json()["empresa"]["ambiente"] == "simulacion"


def test_un_rut_de_demostracion_no_sale_de_demo(sin_empresa, admin, sii_prohibido, monkeypatch):
    """El SII lista 77777777-7 como autorizado incluso en producción: el bloqueo es local y va
    antes de consultar al SII (sii_prohibido falla si se le pregunta)."""
    import core.empresa
    monkeypatch.setattr(core.empresa, "RUTS_SOLO_DEMO", frozenset({RUT_EMISOR}))
    assert sin_empresa.put("/api/v1/empresa", headers=admin,
                           json={"rut": RUT_EMISOR, "razon_social": "EMPRESA DEMO SPA", **DATOS}).status_code == 200
    r = _pasar(sin_empresa, admin, "certificacion")
    assert r.status_code == 409 and r.json()["error"]["codigo"] == "requisitos_certificacion"
    assert [f["codigo"] for f in r.json()["error"]["detalle"]["falta"]] == ["rut_ficticio"]
    r = _pasar(sin_empresa, admin, "produccion")
    assert r.status_code == 409 and "rut_ficticio" in {f["codigo"] for f in r.json()["error"]["detalle"]["falta"]}
    assert sin_empresa.get("/api/v1/empresa", headers=admin).json()["ambiente"] == "simulacion"
