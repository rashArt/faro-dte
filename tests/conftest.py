"""
tests/conftest.py — Instancia limpia de Faro para la suite pytest.

Antes de importar `core` (su `settings` se lee una sola vez al importar) deja el entorno
apuntando a una carpeta temporal: base SQLite propia, `storage/` propio, secretos de prueba
y los programadores de fondo apagados. Nunca toca la base ni el `storage/` del repositorio.

`FARO_TEST_DATABASE_URL` cambia la base de la suite a PostgreSQL. Esa base se vacía entera al
empezar, así que se exige además `FARO_TEST_PG_DESECHABLE=1` (nunca apuntes a una base con datos).

Datos ficticios (L14): EMPRESA DEMO SPA 77777777-7, CAF y certificado de tests/dorado/fixtures.
"""
from __future__ import annotations

import base64
import os
import shutil
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
FIXTURES = RAIZ / "tests" / "dorado" / "fixtures"
TMP = Path(tempfile.mkdtemp(prefix="faro-pytest-"))

os.environ.update({
    "DATABASE_URL": os.environ.get("FARO_TEST_DATABASE_URL") or f"sqlite:///{(TMP / 'faro.db').as_posix()}",
    "STORAGE_PATH": str(TMP / "storage"),
    "JWT_SECRET_KEY": "clave-de-prueba-jwt-" + "x" * 32,
    "DTE_MASTER_KEY": base64.urlsafe_b64encode(b"clave-de-prueba-fernet-32-bytes!").decode(),
    "ADMIN_PASSWORD": "clave-de-prueba-admin",
    "SII_AMBIENTE": "certificacion",
    "RVD_SCHEDULER_ACTIVO": "false",
    "FOLIOS_AUTO_ACTIVO": "false",
    "DEBUG": "false",
})

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

RUT_EMISOR = "77777777-7"
RUT_RECEPTOR = "76111111-6"
CLAVE_CERT = "demo"
TABLAS_POR_PRUEBA = ("emisiones", "dtes", "cafs", "audit_log")


@pytest.fixture(autouse=True)
def _sin_red(monkeypatch):
    """La suite no habla con el SII: la ficha pública de la empresa responde una empresa
    ficticia autorizada, sin resolución (la emisión usa la del ambiente)."""
    import core.negocios

    def ficha(rut: str, *a, **k) -> dict:
        return {"rut": rut, "razon_social": "EMPRESA DEMO SPA", "autorizado": True, "tipos": [],
                "resolucion": {"numero": 0, "fecha": ""}}
    monkeypatch.setattr(core.negocios, "info_empresa", ficha)


@pytest.fixture(scope="session")
def cliente():
    """La app completa con su arranque (`lifespan`): crea el esquema y el usuario admin.
    Los errores no manejados responden 500, como en producción, en vez de subir a la prueba."""
    from core import datos
    if datos.es_postgres():  # base desechable de pruebas: se vacía entera al empezar
        if os.environ.get("FARO_TEST_PG_DESECHABLE") != "1":
            pytest.exit("La suite vacía la base PostgreSQL de FARO_TEST_DATABASE_URL. Si es desechable, "
                        "define FARO_TEST_PG_DESECHABLE=1.", returncode=2)
        with datos.motor().begin() as con:
            con.exec_driver_sql("DROP SCHEMA public CASCADE")
            con.exec_driver_sql("CREATE SCHEMA public")
    import main
    with TestClient(main.app, raise_server_exceptions=False) as c:
        yield c
    shutil.rmtree(TMP, ignore_errors=True)


@pytest.fixture(scope="session")
def admin(cliente) -> dict:
    """Cabecera `Authorization` del administrador."""
    r = cliente.post("/api/v1/auth/login", json={"username": "admin", "password": "clave-de-prueba-admin"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def instancia(cliente, admin):
    """Emisión desde cero: sin documentos, sin CAF, `storage/` vacío y CAF ficticios cargados."""
    from core.database import get_db
    with get_db() as con:
        for tabla in TABLAS_POR_PRUEBA:
            con.execute(f"DELETE FROM {tabla}")
    for sub in ("dtes", "pdfs"):
        shutil.rmtree(TMP / "storage" / sub, ignore_errors=True)
    for tipo in (33, 56, 61):
        with open(FIXTURES / f"caf_{tipo}.xml", "rb") as f:
            r = cliente.post("/api/v1/db/cafs", headers=admin, files={"archivo": (f"caf_{tipo}.xml", f, "text/xml")})
        assert r.status_code in (200, 201), r.text
    return cliente


@pytest.fixture(scope="session")
def cert_b64() -> str:
    return base64.b64encode((FIXTURES / "cert_demo.p12").read_bytes()).decode()


@pytest.fixture
def cuerpo(cert_b64):
    """Fábrica del JSON de `POST /api/v1/dte/emitir` (factura afecta por defecto)."""
    def _cuerpo(nombre: str = "Servicio de prueba", tipo: int = 33, folio: int = 0, **dte) -> dict:
        datos = {
            "tipo_dte": tipo, "folio": folio,
            "emisor": {"rut": RUT_EMISOR, "razon_social": "EMPRESA DEMO SPA", "giro": "Cafeteria",
                       "codigo_actividad": 561000, "direccion": "Calle Ficticia 123",
                       "comuna": "Santiago", "ciudad": "Santiago"},
            "receptor": {"rut": RUT_RECEPTOR, "razon_social": "CLIENTE DEMO LTDA", "giro": "Comercio",
                         "direccion": "Av Siempreviva 742", "comuna": "Santiago"},
            "items": [{"numero_linea": 1, "nombre": nombre, "cantidad": 1, "precio_unitario": 10000}],
        }
        datos.update(dte)
        return {"dte": datos, "certificado_p12_b64": cert_b64, "password_certificado": CLAVE_CERT}
    return _cuerpo


def folio_siguiente(tipo: int = 33) -> int:
    from core.database import obtenerUno
    return obtenerUno("SELECT folio_siguiente FROM cafs WHERE rut_emisor=? AND tipo_dte=?",
                      (RUT_EMISOR, tipo))[0]
