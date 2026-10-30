"""
Adaptadores del fork (docs/deploy/PLAN.md, fase 3, hallazgo #13): caché y clientes del SII.

- El token del SII se pide una vez por certificado, ambiente y servicio, no en cada petición.
- Facturas y boletas tienen tokens distintos (L9).
- El cliente del adaptador no escribe en la base; el `ClienteSII` de siempre sí.
- Con Redis (`FARO_TEST_REDIS_URL`), los valores quedan cifrados.
"""
from __future__ import annotations

import base64
import os
from datetime import datetime, timedelta

import pytest

from tests.conftest import CLAVE_CERT, FIXTURES


@pytest.fixture
def cert():
    from core.crypto import CertificadoDigital
    return CertificadoDigital((FIXTURES / "cert_demo.p12").read_bytes(), CLAVE_CERT)


@pytest.fixture(params=["memoria", "redis"])
def cache_limpia(request):
    """La misma prueba con la caché en memoria y, si hay `FARO_TEST_REDIS_URL`, con Redis."""
    from core.adaptadores import cache
    if request.param == "redis":
        url = os.environ.get("FARO_TEST_REDIS_URL")
        if not url:
            pytest.skip("sin FARO_TEST_REDIS_URL")
        import redis
        redis.Redis.from_url(url).flushdb()
        cache.reiniciar(cache.CacheRedis(url))
    else:
        cache.reiniciar(cache.CacheMemoria())
    yield cache.cache()
    cache.reiniciar()


@pytest.fixture
def tokens(monkeypatch):
    """Reemplaza el login contra el SII (semilla → firma → token) por un contador."""
    from core.sii import ClienteSII
    from core.sii_boleta import ClienteBoletaSII
    pedidos: list[str] = []

    def falso(servicio):
        def obtener_token(self):
            if self._token and self._token_expira and datetime.now() < self._token_expira:
                return self._token
            pedidos.append(servicio)
            self._token, self._token_expira = f"token-{servicio}-{len(pedidos)}", datetime.now() + timedelta(minutes=50)
            return self._token
        return obtener_token
    monkeypatch.setattr(ClienteSII, "obtener_token", falso("factura"))
    monkeypatch.setattr(ClienteBoletaSII, "obtener_token", falso("boleta"))
    return pedidos


def test_cache_memoria_vence(monkeypatch):
    from core.adaptadores import cache
    reloj = [1000.0]
    monkeypatch.setattr(cache.time, "monotonic", lambda: reloj[0])
    c = cache.CacheMemoria()
    c.poner("k", "v", 60)
    assert c.obtener("k") == "v"
    reloj[0] += 61
    assert c.obtener("k") is None


def test_contador_de_intentos(cache_limpia):
    """Los límites de login (fase 4) cuentan en la caché: memoria o Redis."""
    assert cache_limpia.incrementar("intentos", 60) == 1
    assert cache_limpia.incrementar("intentos", 60) == 2
    assert 0 < cache_limpia.restante("intentos") <= 60
    assert cache_limpia.restante("no-existe") == 0
    cache_limpia.borrar("intentos")
    assert cache_limpia.incrementar("intentos", 60) == 1


def test_token_se_pide_una_vez_por_servicio(cert, cache_limpia, tokens):
    from core.adaptadores.sii import ClienteBoletas, ClienteFacturas
    from core.sii import AmbienteSII
    for _ in range(3):
        with ClienteFacturas(cert, AmbienteSII.CERTIFICACION) as c:
            assert c.obtener_token() == "token-factura-1"
    with ClienteBoletas(cert, AmbienteSII.CERTIFICACION) as c:
        assert c.obtener_token() == "token-boleta-2"
    assert tokens == ["factura", "boleta"]


def test_olvidar_token_pide_uno_nuevo(cert, cache_limpia, tokens):
    from core.adaptadores.sii import ClienteFacturas
    from core.sii import AmbienteSII
    with ClienteFacturas(cert, AmbienteSII.CERTIFICACION) as c:
        c.obtener_token()
        c.olvidar_token()
    with ClienteFacturas(cert, AmbienteSII.CERTIFICACION) as c:
        assert c.obtener_token() == "token-factura-2"


class _RespuestaFalsa:
    status_code = 200
    text = "<RECEPCIONDTE><STATUS>0</STATUS><TRACKID>123456</TRACKID></RECEPCIONDTE>"

    def raise_for_status(self):
        return None


class _HttpFalso:
    is_closed = False

    def post(self, *a, **k):
        return _RespuestaFalsa()

    def close(self):
        self.is_closed = True


def _estado(dte_id: int) -> str:
    from core.database import obtenerUno
    return obtenerUno("SELECT estado FROM dtes WHERE id=?", (dte_id,))["estado"]


def test_el_adaptador_no_escribe_en_la_base_y_el_cliente_de_siempre_si(
        instancia, admin, cuerpo, cert, cache_limpia, tokens, monkeypatch):
    from core.adaptadores.sii import ClienteFacturas
    from core.sii import AmbienteSII, ClienteSII
    monkeypatch.setattr(ClienteSII, "_cli", lambda self: _HttpFalso())
    j = instancia.post("/api/v1/dte/emitir", json=cuerpo(), headers=admin).json()
    sobre = base64.b64decode(j["xml_envio_b64"])

    with ClienteFacturas(cert, AmbienteSII.CERTIFICACION) as c:
        assert c.enviar_dte(sobre, "77777777", "7")[0] == 123456
    assert _estado(j["dte_id"]) == "generado"

    with ClienteSII(cert, AmbienteSII.CERTIFICACION) as c:
        c.enviar_dte(sobre, "77777777", "7")
    assert _estado(j["dte_id"]) == "enviado"


def test_ruta_enviar_sigue_marcando_el_documento(instancia, admin, cuerpo, cert_b64, cache_limpia, tokens, monkeypatch):
    from core.sii import ClienteSII
    monkeypatch.setattr(ClienteSII, "_cli", lambda self: _HttpFalso())
    j = instancia.post("/api/v1/dte/emitir", json=cuerpo(), headers=admin).json()
    r = instancia.post("/api/v1/dte/enviar", headers=admin, json={
        "xml_envio_b64": j["xml_envio_b64"], "certificado_p12_b64": cert_b64,
        "password_certificado": CLAVE_CERT, "rut_empresa": "77777777", "dv_empresa": "7"})
    assert r.status_code == 200, r.text
    assert r.json()["track_id"] == 123456
    assert _estado(j["dte_id"]) == "enviado"


@pytest.mark.skipif(not os.environ.get("FARO_TEST_REDIS_URL"), reason="sin FARO_TEST_REDIS_URL")
def test_redis_guarda_cifrado():
    import redis
    from core.adaptadores.cache import PREFIJO, CacheRedis
    url = os.environ["FARO_TEST_REDIS_URL"]
    c = CacheRedis(url)
    c.poner("prueba", "token-secreto", 30)
    assert c.obtener("prueba") == "token-secreto"
    assert b"token-secreto" not in redis.Redis.from_url(url).get(PREFIJO + "prueba")
    c.borrar("prueba")
    assert c.obtener("prueba") is None
