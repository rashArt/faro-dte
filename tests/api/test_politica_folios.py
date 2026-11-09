"""
Política de folios con `id_externo` (docs/deploy/PLAN.md, fase 2): timbrado = emitido.

- El mismo JSON con el mismo `id_externo` devuelve el documento ya emitido.
- Un documento timbrado no cambia: otro JSON con el mismo `id_externo` responde 409.
- Un intento que falla antes de quedar timbrado deja la fila en `error`; su folio lo retoma el
  mismo `id_externo` o, si no vuelve, la venta siguiente.
- Una reserva en curso responde 409; una reserva huérfana (vencida) se reusa.
"""
from __future__ import annotations

import threading

import pytest
from sqlalchemy.exc import IntegrityError

from tests.conftest import CLAVE_CERT, FIXTURES, RUT_EMISOR, folio_siguiente


def _emitir(cliente, cabecera, cuerpo, id_externo=None):
    if id_externo:
        cuerpo = {**cuerpo, "id_externo": id_externo}
    return cliente.post("/api/v1/dte/emitir", json=cuerpo, headers=cabecera)


def _fila(dte_id: int) -> dict:
    from core.database import obtenerUno
    return dict(obtenerUno("SELECT * FROM dtes WHERE id=?", (dte_id,)))


def test_json_distinto_con_documento_timbrado_responde_409(instancia, admin, cuerpo):
    a = _emitir(instancia, admin, cuerpo("Venta original"), "pedido-200")
    assert a.status_code == 200, a.text
    b = _emitir(instancia, admin, cuerpo("Venta cambiada"), "pedido-200")
    assert b.status_code == 409
    assert b.json()["error"]["codigo"] == "ya_emitido"
    assert b.json()["error"]["detalle"]["folio"] == a.json()["folio"]
    assert folio_siguiente() == 2


def test_reintento_devuelve_el_mismo_documento_y_lo_marca_repetido(instancia, admin, cuerpo):
    a = _emitir(instancia, admin, cuerpo(), "pedido-201").json()
    b = _emitir(instancia, admin, cuerpo(), "pedido-201").json()
    assert (b["dte_id"], b["folio"], b["repetido"], a["repetido"]) == (a["dte_id"], a["folio"], True, False)
    assert b["xml_envio_b64"] == a["xml_envio_b64"]


def test_falla_tras_reservar_deja_el_folio_para_el_mismo_id_externo(instancia, admin, cuerpo, monkeypatch):
    from core.orchestrator import OrquestadorDTE
    original = OrquestadorDTE.generar_pdf
    monkeypatch.setattr(OrquestadorDTE, "generar_pdf", lambda *a, **k: (_ for _ in ()).throw(OSError("disco lleno")))
    r = _emitir(instancia, admin, cuerpo("Con error"), "pedido-202")
    assert r.status_code == 500
    detalle = r.json()["error"]["detalle"]
    assert detalle["estado"] == "error" and detalle["folio"] == 1
    assert _fila(detalle["dte_id"])["motivo_error"] == "OSError: disco lleno"

    monkeypatch.setattr(OrquestadorDTE, "generar_pdf", original)
    r = _emitir(instancia, admin, cuerpo("Corregido"), "pedido-202")   # corrección: no estaba timbrado
    assert r.status_code == 200, r.text
    j = r.json()
    assert (j["dte_id"], j["folio"], j["estado"]) == (detalle["dte_id"], 1, "generado")
    assert _fila(j["dte_id"])["intento"] == 2
    assert folio_siguiente() == 2


def test_folio_en_error_lo_toma_la_venta_siguiente(instancia, admin, cuerpo, monkeypatch):
    from core.orchestrator import OrquestadorDTE
    original = OrquestadorDTE.generar_pdf
    monkeypatch.setattr(OrquestadorDTE, "generar_pdf", lambda *a, **k: (_ for _ in ()).throw(OSError("disco lleno")))
    perdido = _emitir(instancia, admin, cuerpo(), "pedido-203").json()["error"]["detalle"]
    monkeypatch.setattr(OrquestadorDTE, "generar_pdf", original)

    otra = _emitir(instancia, admin, cuerpo("Otra venta"), "pedido-204").json()
    assert (otra["folio"], otra["dte_id"]) == (perdido["folio"], perdido["dte_id"])
    assert folio_siguiente() == 2
    # La venta que falló vuelve: su folio ya es de otra, así que toma uno nuevo.
    vuelve = _emitir(instancia, admin, cuerpo(), "pedido-203").json()
    assert vuelve["folio"] == 2 and vuelve["dte_id"] != otra["dte_id"]


def test_reserva_en_curso_responde_409_y_huerfana_se_reusa(instancia, admin, cuerpo, monkeypatch):
    from core.servicios import emision
    from core.database import ejecutar
    monkeypatch.setattr(emision, "_generar", lambda *a, **k: None)   # el proceso "cae" tras reservar
    r = _emitir(instancia, admin, cuerpo(), "pedido-205")
    assert r.json()["estado"] == "reservado"
    monkeypatch.undo()

    en_curso = _emitir(instancia, admin, cuerpo(), "pedido-205")
    assert en_curso.status_code == 409 and en_curso.json()["error"]["codigo"] == "en_curso"

    ejecutar("UPDATE dtes SET actualizado_en='2000-01-01T00:00:00.000000' WHERE id=?", (r.json()["dte_id"],))
    retomada = _emitir(instancia, admin, cuerpo(), "pedido-205")
    assert retomada.status_code == 200, retomada.text
    assert (retomada.json()["folio"], retomada.json()["estado"]) == (1, "generado")


def test_id_externo_concurrente_gasta_un_solo_folio(instancia, cuerpo):
    from core.crypto import CertificadoDigital
    from core.dte import DTEInput
    from core.errors import ConflictoError
    from core.servicios import emision
    cert = CertificadoDigital((FIXTURES / "cert_demo.p12").read_bytes(), CLAVE_CERT)
    quien = emision.Solicitante(origen="usuario:prueba", cuenta_id=None, es_admin=False)
    dte = cuerpo()["dte"]
    resultados, choques = [], []

    def una():
        try:
            resultados.append(emision.emitir(DTEInput(**dte), quien, id_externo="pedido-206", certificado=cert))
        except ConflictoError as e:
            choques.append(e.codigo)

    hilos = [threading.Thread(target=una) for _ in range(8)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert {r["dte_id"] for r in resultados} and len({r["dte_id"] for r in resultados}) == 1
    assert set(choques) <= {"en_curso"}
    assert folio_siguiente() == 2


def test_folio_explicito_es_solo_del_administrador(instancia, admin, cuerpo):
    r = instancia.post("/api/v1/apikeys", json={"nombre": "integracion-prueba"}, headers=admin)
    assert r.status_code in (200, 201), r.text
    clave = {"Authorization": f"Bearer {r.json()['api_key']}"}
    assert _emitir(instancia, clave, cuerpo(folio=1)).status_code == 403
    assert _emitir(instancia, clave, cuerpo()).status_code == 200
    assert folio_siguiente() == 2


def test_folio_explicito_adelante_salta_el_contador_y_atras_responde_409(instancia, admin, cuerpo):
    assert _emitir(instancia, admin, cuerpo(folio=5)).json()["folio"] == 5
    assert folio_siguiente() == 6
    r = _emitir(instancia, admin, cuerpo(folio=3))
    assert r.status_code == 409 and folio_siguiente() == 6


def test_id_externo_es_propio_de_cada_credencial(instancia, admin, cuerpo):
    r = instancia.post("/api/v1/apikeys", json={"nombre": "otra-integracion"}, headers=admin)
    clave = {"Authorization": f"Bearer {r.json()['api_key']}"}
    a = _emitir(instancia, admin, cuerpo(), "pedido-207").json()
    b = _emitir(instancia, clave, cuerpo(), "pedido-207").json()
    assert a["dte_id"] != b["dte_id"]


def test_sin_certificado_en_el_cuerpo_usa_el_del_negocio(instancia, admin, cuerpo):
    with open(FIXTURES / "cert_demo.p12", "rb") as f:
        r = instancia.post("/api/v1/keystore/certificados", headers=admin,
                           files={"archivo": ("cert_demo.p12", f, "application/x-pkcs12")},
                           data={"password": CLAVE_CERT})
    assert r.status_code in (200, 201), r.text
    cert_id = r.json()["id"]
    negocios = instancia.get("/api/v1/keystore/negocios", headers=admin).json()
    negocios = negocios.get("negocios", negocios) if isinstance(negocios, dict) else negocios
    if not any(n["rut"] == RUT_EMISOR for n in negocios):
        r = instancia.post("/api/v1/keystore/negocios", headers=admin, data={"rut": RUT_EMISOR, "cert_id": cert_id})
        assert r.status_code in (200, 201), r.text
    c = cuerpo()
    del c["certificado_p12_b64"], c["password_certificado"]
    r = _emitir(instancia, admin, c)
    assert r.status_code == 200, r.text


def test_rvd_no_cuenta_filas_sin_timbre(instancia):
    from datetime import date
    from core.database import ejecutar
    from core.rvd import boletas_del_dia
    for folio, estado in ((1, "generado"), (2, "error"), (3, "reservado")):
        ejecutar("INSERT INTO dtes (tipo_dte, folio, rut_emisor, rut_receptor, fecha_emision, monto_total, "
                 "estado, creado_en, actualizado_en) VALUES (39, ?, ?, '66666666-6', '2026-10-29', 1190, ?, "
                 "'2026-10-29T10:00:00', '2026-10-29T10:00:00')", (folio, RUT_EMISOR, estado))
    assert [b["folio"] for b in boletas_del_dia(RUT_EMISOR, date(2026, 10, 29))] == [1]


def test_integrity_error_responde_409_sin_detalle_de_sql(instancia, admin, monkeypatch):
    import api.routes.auth as rutas_auth

    def choca(**_):
        raise IntegrityError("INSERT INTO usuarios …", {}, Exception("UNIQUE constraint failed: usuarios.username"))
    monkeypatch.setattr(rutas_auth, "crear_usuario", choca)
    r = instancia.post("/api/v1/auth/registro", json={"username": "persona_demo", "password": "clave-de-prueba-1"},
                       headers=admin)
    assert r.status_code == 409 and r.json()["error"]["codigo"] == "conflicto"
    assert "INSERT" not in r.text and "UNIQUE" not in r.text


@pytest.mark.parametrize("ruta", ["/api/v1/keystore/negocios/1/contexto-emision",
                                  f"/api/v1/mipyme/contexto-siiengine/{RUT_EMISOR}"])
def test_rutas_con_secretos_exigen_la_clave_de_la_app(cliente, admin, ruta):
    assert cliente.get(ruta).status_code == 401
    assert cliente.get(ruta, headers=admin).status_code == 403


def test_una_api_key_no_registra_ni_desactiva_usuarios(cliente, admin):
    r = cliente.post("/api/v1/apikeys", json={"nombre": "sin-permisos"}, headers=admin)
    clave = {"Authorization": f"Bearer {r.json()['api_key']}"}
    assert cliente.post("/api/v1/auth/registro", json={"username": "intruso2", "password": "clave-de-prueba-2"},
                        headers=clave).status_code == 403
    assert cliente.delete("/api/v1/auth/usuarios/1", headers=clave).status_code == 403
    r = cliente.post("/api/v1/auth/registro", json={"username": "persona_demo2", "password": "clave-de-prueba-3"},
                     headers=admin)
    assert r.status_code == 201, r.text
