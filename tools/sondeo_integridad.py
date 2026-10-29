#!/usr/bin/env python3
"""Sondeo de integridad de la API de emisión: duplicados, idempotencia, folios, registro abierto.

Reproduce los hallazgos del 2026-10-15 (docs/deploy/PLAN.md, tabla Verificación). Es un sondeo, no la
suite: cada prueba imprime OK o HALLAZGO con la evidencia, contra una instancia viva. Las mismas
pruebas están en `tests/api/` (pytest); este sondeo sirve para medir una imagen. Sin `id_externo`, cada
petición es un documento nuevo a propósito: la idempotencia se pide con ese campo (fase 2).

⚠️ DESTRUCTIVO: carga un CAF ficticio de EMPRESA DEMO SPA, crea un usuario `intruso` y deja la
base con folios desordenados. Corre SOLO contra una instancia desechable, nunca contra la local
que usas ni contra un cliente:

    docker run -d --name faro-sondeo -e ADMIN_PASSWORD=clave-de-prueba-sondeo \\
      -e DATABASE_URL=sqlite:////app/storage/dte_database.db faro-dte:local
    docker exec -e FARO_SONDEO_DESECHABLE=1 faro-sondeo python tools/sondeo_integridad.py
    docker rm -f faro-sondeo

Variables: FARO_URL (http://localhost:8000), FARO_USUARIO (admin), FARO_CLAVE (clave-de-prueba-sondeo),
FARO_DB (ruta del SQLite de la instancia, para leer folio_siguiente).
"""
from __future__ import annotations

import base64
import hashlib
import os
import sqlite3
import sys
import threading
from pathlib import Path

import httpx

RAIZ = Path(__file__).resolve().parent.parent
FIXTURES = RAIZ / "tests" / "dorado" / "fixtures"
B = os.environ.get("FARO_URL", "http://localhost:8000")
DB = os.environ.get("FARO_DB", "/app/storage/dte_database.db")
hallazgos: list[str] = []


def q(sql: str):
    con = sqlite3.connect(DB)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def siguiente() -> int:
    return q("SELECT folio_siguiente FROM cafs WHERE rut_emisor='77777777-7' AND tipo_dte=33")[0][0]


def veredicto(ok: bool, texto: str) -> None:
    print(("  OK       " if ok else "  HALLAZGO ") + texto)
    if not ok:
        hallazgos.append(texto)


def login(usuario: str, clave: str) -> str | None:
    return httpx.post(f"{B}/api/v1/auth/login", json={"username": usuario, "password": clave}).json().get("access_token")


def main() -> int:
    if os.environ.get("FARO_SONDEO_DESECHABLE") != "1":
        print("Este sondeo ensucia la base. Define FARO_SONDEO_DESECHABLE=1 y úsalo en una instancia desechable.")
        return 2
    token = login(os.environ.get("FARO_USUARIO", "admin"), os.environ.get("FARO_CLAVE", "clave-de-prueba-sondeo"))
    if not token:
        print("No se pudo iniciar sesión.")
        return 2
    h = {"Authorization": f"Bearer {token}"}
    p12 = base64.b64encode((FIXTURES / "cert_demo.p12").read_bytes()).decode()

    def cuerpo(nombre: str, folio: int = 0) -> dict:
        return {"dte": {"tipo_dte": 33, "folio": folio,
                        "emisor": {"rut": "77777777-7", "razon_social": "EMPRESA DEMO SPA", "giro": "Cafeteria",
                                   "codigo_actividad": 561000, "direccion": "Calle Ficticia 123",
                                   "comuna": "Santiago", "ciudad": "Santiago"},
                        "receptor": {"rut": "76111111-6", "razon_social": "CLIENTE DEMO LTDA", "giro": "Comercio",
                                     "direccion": "Av Siempreviva 742", "comuna": "Santiago"},
                        "items": [{"numero_linea": 1, "nombre": nombre, "cantidad": 1, "precio_unitario": 10000}]},
                "certificado_p12_b64": p12, "password_certificado": "demo"}

    def emitir(c: dict):
        try:
            r = httpx.post(f"{B}/api/v1/dte/emitir", json=c, headers=h, timeout=120)
            return r.status_code, r.json()
        except httpx.HTTPError as e:
            return "conexion", {"error": repr(e)[:80]}

    with open(FIXTURES / "caf_33.xml", "rb") as f:
        r = httpx.post(f"{B}/api/v1/db/cafs", headers=h, files={"archivo": ("caf_33.xml", f, "text/xml")})
    print(f"CAF ficticio T33 (1-100): {r.status_code}")

    print("\n1. Registro de usuarios sin token")
    r = httpx.post(f"{B}/api/v1/auth/registro", json={"username": "intruso", "password": "clave-de-prueba-intruso"})
    veredicto(r.status_code in (401, 403, 404), f"POST /auth/registro sin Authorization -> {r.status_code}")
    if r.status_code == 201:
        ti = {"Authorization": f"Bearer {login('intruso', 'clave-de-prueba-intruso')}"}
        r2 = httpx.post(f"{B}/api/v1/apikeys", json={"nombre": "del-intruso"}, headers=ti)
        r3 = httpx.get(f"{B}/api/v1/db/dtes", headers=ti)
        veredicto(r2.status_code >= 400 and r3.status_code >= 400,
                  f"el usuario recién registrado crea API keys ({r2.status_code}) y lista documentos ({r3.status_code})")

    print("\n2. 20 emisiones simultáneas, folio automático")
    resultados: list = []
    hilos = [threading.Thread(target=lambda i=i: resultados.append(emitir(cuerpo(f"Concurrente {i}")))) for i in range(20)]
    [t.start() for t in hilos]
    [t.join() for t in hilos]
    folios = sorted(j["folio"] for s, j in resultados if s == 200)
    veredicto(len(folios) == 20 and len(set(folios)) == 20 and folios == list(range(folios[0], folios[0] + 20)),
              f"{len(folios)}/20 emitidas, folios únicos y consecutivos: {folios[0]}..{folios[-1]}" if folios else "sin emisiones")

    print("\n3. Reintento de la MISMA petición con su id_externo (el producto no recibió la respuesta)")
    c = cuerpo("Factura pedido #123")
    c["id_externo"] = "pedido-123"
    (_, a), (_, b) = emitir(c), emitir(c)
    veredicto(a.get("folio") == b.get("folio"), f"1er intento folio {a.get('folio')}, reintento folio {b.get('folio')}")

    print("\n4. Folio explícito ya usado (folio=1)")
    antes = siguiente()
    pdf = q("SELECT pdf_path FROM dtes WHERE tipo_dte=33 AND folio=1")[0][0]
    huella = hashlib.sha256(Path(pdf).read_bytes()).hexdigest() if Path(pdf).exists() else None
    s, _ = emitir(cuerpo("Repite el folio 1", folio=1))
    despues = siguiente()
    veredicto(s == 409, f"respuesta {s} (esperado 409 conflicto)")
    veredicto(despues >= antes, f"folio_siguiente {antes} -> {despues} (nunca debe retroceder)")
    if huella:
        veredicto(hashlib.sha256(Path(pdf).read_bytes()).hexdigest() == huella, "el PDF del folio 1 original no se sobrescribe")
    s, j = emitir(cuerpo("Automático después del explícito"))
    veredicto(s == 200, f"la siguiente emisión automática responde {s}")

    print("\n5. Validación XSD antes de guardar (GiroRecep: máximo 40 caracteres)")
    con = sqlite3.connect(DB)
    con.execute("UPDATE cafs SET folio_siguiente=(SELECT MAX(folio)+1 FROM dtes WHERE tipo_dte=33) WHERE tipo_dte=33")
    con.commit()
    con.close()
    c = cuerpo("Giro largo")
    c["dte"]["receptor"]["giro"] = "G" * 80
    antes = siguiente()
    s, j = emitir(c)
    veredicto(s == 422 and siguiente() == antes, f"respuesta {s}, folio_siguiente {antes} -> {siguiente()} "
              "(esperado 422 sin consumir folio)")

    print(f"\n{len(hallazgos)} hallazgo(s).")
    return 1 if hallazgos else 0


if __name__ == "__main__":
    sys.exit(main())
