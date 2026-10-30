"""
Roles, tokens, auditoría y límites (docs/deploy/PLAN.md, fase 4).

- Un token (`integracion`) emite, envía, consulta y gestiona CAF y certificados; recibe 403 en
  usuarios, tokens, auditoría, listado general de documentos, `/db`, simulación, MIPYME y onboarding.
- Un token ve solo los documentos que emitió (404 para el resto).
- Renovar conserva el `id` (y el `id_externo`); la clave anterior y un token revocado dan 401.
- Login: el sexto intento fallido da 429; cambiar la contraseña invalida los JWT anteriores.
- La auditoría registra el actor de cada petición que cambia algo, sin el cuerpo.
"""
from __future__ import annotations

import pytest

from tests.conftest import FIXTURES, RUT_EMISOR


def _token(cliente, admin, nombre: str = "punto-de-venta") -> tuple[dict, int]:
    r = cliente.post("/api/v1/apikeys", json={"nombre": nombre}, headers=admin)
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['api_key']}"}, r.json()["id"]


def _emitir(cliente, cabecera, cuerpo, id_externo=None):
    if id_externo:
        cuerpo = {**cuerpo, "id_externo": id_externo}
    return cliente.post("/api/v1/dte/emitir", json=cuerpo, headers=cabecera)


@pytest.fixture
def token(instancia, admin):
    return _token(instancia, admin)


# ---- Matriz de permisos ----

NEGADO_AL_TOKEN = [
    ("POST", "/api/v1/auth/registro"),   # cuerpo válido abajo: si no, responde 422 antes
    ("GET", "/api/v1/auth/usuarios"),
    ("DELETE", "/api/v1/auth/usuarios/1"),
    ("GET", "/api/v1/apikeys"),
    ("POST", "/api/v1/apikeys"),
    ("DELETE", "/api/v1/apikeys/1"),
    ("POST", "/api/v1/apikeys/1/renovar"),
    ("GET", "/api/v1/auditoria"),
    ("GET", "/api/v1/dte/documentos"),
    ("GET", "/api/v1/dte/documentos/emisores"),
    ("GET", "/api/v1/db/dtes"),
    ("GET", "/api/v1/db/cafs"),
    ("POST", "/api/v1/db/cafs"),
    ("GET", "/api/v1/db/logs"),
    ("GET", "/api/v1/simulacion/estado"),
    ("GET", "/api/v1/onboarding/diagnostico"),
    ("POST", "/api/v1/mipyme/diagnostico"),
]


@pytest.fixture(scope="module")
def token_sin_datos(cliente, admin):
    return _token(cliente, admin, "matriz")[0]


@pytest.mark.parametrize("metodo,ruta", NEGADO_AL_TOKEN)
def test_token_recibe_403_fuera_de_su_rol(cliente, token_sin_datos, metodo, ruta):
    r = cliente.request(metodo, ruta, headers=token_sin_datos,
                        json={"username": "intruso_token", "password": "clave-de-prueba-larga", "nombre": "otra"})
    assert r.status_code == 403, (metodo, ruta, r.status_code, r.text)


def test_token_opera_emite_y_gestiona_caf_y_certificados(instancia, token, cuerpo):
    cabecera, _ = token
    r = _emitir(instancia, cabecera, cuerpo())
    assert r.status_code == 200, r.text
    with open(FIXTURES / "caf_39.xml", "rb") as f:
        r = instancia.post("/api/v1/caf", headers=cabecera, files={"archivo": ("caf_39.xml", f, "text/xml")})
    assert r.status_code == 200, r.text
    assert instancia.get("/api/v1/caf", headers=cabecera).status_code == 200
    assert instancia.get(f"/api/v1/caf/activo/{RUT_EMISOR}/33", headers=cabecera).status_code == 200
    assert instancia.get(f"/api/v1/caf/folios/siguiente/{RUT_EMISOR}/33", headers=cabecera).json()["folio_disponible"] == 2
    assert instancia.get("/api/v1/keystore/certificados", headers=cabecera).status_code == 200
    me = instancia.get("/api/v1/auth/me", headers=cabecera).json()
    assert me["tipo"] == "apikey" and me["rol"] == "integracion"


def test_token_gestiona_el_keystore_de_la_empresa(instancia, admin, token):
    cabecera, _ = token
    with open(FIXTURES / "cert_demo.p12", "rb") as f:
        r = instancia.post("/api/v1/keystore/certificados", headers=cabecera,
                           files={"archivo": ("cert_demo.p12", f, "application/x-pkcs12")},
                           data={"password": "demo", "nombre": "subido por el token"})
    assert r.status_code == 201, r.text
    cert_id = r.json()["id"]

    def ids(cab):
        j = instancia.get("/api/v1/keystore/certificados", headers=cab).json()
        return {c["id"] for c in (j.get("certificados", j) if isinstance(j, dict) else j)}
    assert cert_id in ids(admin) and cert_id in ids(cabecera)
    assert instancia.delete(f"/api/v1/keystore/certificados/{cert_id}", headers=cabecera).status_code == 200


def test_administrador_usa_todo_y_el_alias_db_responde_lo_mismo(instancia, admin, cuerpo):
    dte_id = _emitir(instancia, admin, cuerpo()).json()["dte_id"]
    for ruta in ("/api/v1/dte/documentos", "/api/v1/auditoria", "/api/v1/auth/usuarios", "/api/v1/apikeys",
                 "/api/v1/db/dtes", "/api/v1/db/logs"):
        assert instancia.get(ruta, headers=admin).status_code == 200, ruta
    pares = [(f"/api/v1/dte/documentos/{dte_id}", f"/api/v1/db/dtes/{dte_id}"),
             (f"/api/v1/dte/documentos/buscar?rut_emisor={RUT_EMISOR}&tipo_dte=33&folio=1",
              f"/api/v1/db/dtes/buscar/{RUT_EMISOR}/33/1"),
             ("/api/v1/caf", "/api/v1/db/cafs"),
             (f"/api/v1/caf/activo/{RUT_EMISOR}/33", f"/api/v1/db/cafs/activo/{RUT_EMISOR}/33"),
             (f"/api/v1/caf/folios/siguiente/{RUT_EMISOR}/33", f"/api/v1/db/folios/siguiente/{RUT_EMISOR}/33")]
    for nueva, alias in pares:
        assert instancia.get(nueva, headers=admin).json() == instancia.get(alias, headers=admin).json(), nueva


def test_alias_db_fuera_de_la_documentacion(cliente):
    rutas = cliente.get("/openapi.json").json()["paths"]
    assert not [r for r in rutas if r.startswith("/api/v1/db")]
    assert "/api/v1/caf" in rutas and "/api/v1/auditoria" in rutas


def test_token_ve_solo_sus_documentos(instancia, admin, token, cuerpo):
    cabecera, _ = token
    propio = _emitir(instancia, cabecera, cuerpo("Venta del token")).json()["dte_id"]
    ajeno = _emitir(instancia, admin, cuerpo("Venta del administrador")).json()
    for sufijo in ("", "/pdf", "/xml"):
        assert instancia.get(f"/api/v1/dte/documentos/{propio}{sufijo}", headers=cabecera).status_code == 200, sufijo
        assert instancia.get(f"/api/v1/dte/documentos/{ajeno['dte_id']}{sufijo}", headers=cabecera).status_code == 404, sufijo
        assert instancia.get(f"/api/v1/dte/documentos/{ajeno['dte_id']}{sufijo}", headers=admin).status_code == 200, sufijo
    r = instancia.get(f"/api/v1/dte/documentos/buscar?rut_emisor={RUT_EMISOR}&tipo_dte=33&folio={ajeno['folio']}",
                      headers=cabecera)
    assert r.status_code == 404
    otro, _ = _token(instancia, admin, "tienda")
    assert instancia.get(f"/api/v1/dte/documentos/{propio}", headers=otro).status_code == 404


# ---- Renovar y revocar ----

def test_renovar_conserva_el_id_externo_y_mata_la_clave_vieja(instancia, token, cuerpo):
    vieja, key_id = token
    a = _emitir(instancia, vieja, cuerpo(), "pedido-401").json()
    r = instancia.post("/api/v1/apikeys/propia/renovar", headers=vieja)
    assert r.status_code == 200, r.text
    assert r.json()["id"] == key_id
    nueva = {"Authorization": f"Bearer {r.json()['api_key']}"}
    assert instancia.get("/api/v1/auth/me", headers=vieja).status_code == 401
    b = _emitir(instancia, nueva, cuerpo(), "pedido-401").json()
    assert (b["dte_id"], b["folio"], b["repetido"]) == (a["dte_id"], a["folio"], True)
    assert instancia.get(f"/api/v1/dte/documentos/{a['dte_id']}", headers=nueva).status_code == 200


def test_administrador_renueva_y_revoca(instancia, admin, token):
    vieja, key_id = token
    r = instancia.post(f"/api/v1/apikeys/{key_id}/renovar", headers=admin)
    assert r.status_code == 200 and r.json()["id"] == key_id
    nueva = {"Authorization": f"Bearer {r.json()['api_key']}"}
    assert instancia.get("/api/v1/auth/me", headers=vieja).status_code == 401
    assert instancia.get("/api/v1/auth/me", headers=nueva).status_code == 200
    assert instancia.delete(f"/api/v1/apikeys/{key_id}", headers=admin).status_code == 200
    assert instancia.get("/api/v1/auth/me", headers=nueva).status_code == 401
    assert instancia.post(f"/api/v1/apikeys/{key_id}/renovar", headers=admin).status_code == 404


def test_renovar_propia_es_solo_para_tokens(cliente, admin):
    assert cliente.post("/api/v1/apikeys/propia/renovar", headers=admin).status_code == 400


# ---- Login, contraseña y Bearer inválidos ----

def _usuario(cliente, admin, nombre: str, clave: str = "clave-de-prueba-larga") -> None:
    r = cliente.post("/api/v1/auth/registro", json={"username": nombre, "password": clave}, headers=admin)
    assert r.status_code in (201, 409), r.text


def _login(cliente, nombre, clave):
    return cliente.post("/api/v1/auth/login", json={"username": nombre, "password": clave})


def test_sexto_login_fallido_bloquea_incluso_con_la_clave_correcta(cliente, admin):
    _usuario(cliente, admin, "persona_bloqueo")
    for _ in range(4):
        assert _login(cliente, "persona_bloqueo", "incorrecta").status_code == 401
    r = _login(cliente, "persona_bloqueo", "incorrecta")
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
    r = _login(cliente, "persona_bloqueo", "clave-de-prueba-larga")
    assert r.status_code == 429 and r.json()["error"]["codigo"] == "demasiadas_solicitudes"
    assert _login(cliente, "admin", "clave-de-prueba-admin").status_code == 200   # otro usuario sigue entrando


def test_usuario_inexistente_responde_igual_que_clave_mala(cliente, admin):
    _usuario(cliente, admin, "persona_existe")
    a, b = _login(cliente, "persona_existe", "incorrecta"), _login(cliente, "nadie_por_aqui", "incorrecta")
    assert a.status_code == b.status_code == 401
    assert a.json()["error"]["mensaje"] == b.json()["error"]["mensaje"]


def test_contrasena_corta_se_rechaza(cliente, admin):
    r = cliente.post("/api/v1/auth/registro", json={"username": "persona_corta", "password": "tu-clave-1"},
                     headers=admin)
    assert r.status_code == 422


def test_cambiar_clave_invalida_los_jwt_anteriores(cliente, admin):
    _usuario(cliente, admin, "persona_cambio")
    viejo = {"Authorization": f"Bearer {_login(cliente, 'persona_cambio', 'clave-de-prueba-larga').json()['access_token']}"}
    r = cliente.post("/api/v1/auth/cambiar-clave", headers=viejo,
                     json={"clave_actual": "clave-de-prueba-larga", "clave_nueva": "otra-clave-de-prueba"})
    assert r.status_code == 200, r.text
    nuevo = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert cliente.get("/api/v1/auth/me", headers=viejo).status_code == 401
    assert cliente.get("/api/v1/auth/me", headers=nuevo).status_code == 200
    assert _login(cliente, "persona_cambio", "clave-de-prueba-larga").status_code == 401
    assert _login(cliente, "persona_cambio", "otra-clave-de-prueba").status_code == 200


def test_cambiar_clave_exige_la_actual(cliente, admin):
    _usuario(cliente, admin, "persona_actual")
    jwt = {"Authorization": f"Bearer {_login(cliente, 'persona_actual', 'clave-de-prueba-larga').json()['access_token']}"}
    r = cliente.post("/api/v1/auth/cambiar-clave", headers=jwt,
                     json={"clave_actual": "no-es-la-actual", "clave_nueva": "otra-clave-de-prueba"})
    assert r.status_code == 401


def test_bearer_invalidos_repetidos_dan_429_y_los_validos_siguen(cliente, admin):
    from core.config import settings
    malo = {"Authorization": "Bearer dte_esto-no-es-un-token"}
    codigos = [cliente.get("/api/v1/auth/me", headers=malo).status_code for _ in range(settings.bearer_max_fallos)]
    assert codigos[:-1] == [401] * (settings.bearer_max_fallos - 1) and codigos[-1] == 429
    assert cliente.get("/api/v1/auth/me", headers=admin).status_code == 200


# ---- Auditoría ----

def test_auditoria_registra_actor_sin_el_cuerpo(instancia, admin, token, cuerpo):
    cabecera, key_id = token
    _emitir(instancia, cabecera, cuerpo(), "pedido-auditado")
    _login(instancia, "admin", "clave-incorrecta")
    filas = instancia.get("/api/v1/auditoria?limite=50", headers=admin).json()
    emision = next(f for f in filas if f["ruta"] == "/api/v1/dte/emitir")
    assert (emision["actor"], emision["metodo"], emision["estado_http"]) == (f"apikey:{key_id}", "POST", 200)
    assert any(f["accion"] == "login_fallido" and f["estado_http"] == 401 for f in filas)
    assert "Servicio de prueba" not in str(filas) and "MII" not in str(filas)   # ni ítems ni el .p12 en base64
    por_actor = instancia.get(f"/api/v1/auditoria?actor=apikey:{key_id}", headers=admin).json()
    assert por_actor and all(f["actor"] == f"apikey:{key_id}" for f in por_actor)
