#!/usr/bin/env python3
"""Genera `http/api/*.http` (extensión REST Client de VS Code) desde el OpenAPI del motor Python.

Un archivo por grupo (tag) de la API, con TODAS las operaciones que publica `/openapi.json`:
cuerpo de ejemplo armado desde el esquema, parámetros de ruta como variables y el login al
inicio de cada archivo. Los archivos generados no se editan a mano; los flujos escritos a mano
van en `http/flujos/`.

    python tools/generar_http.py                                    # desde http://localhost:8000
    python tools/generar_http.py --url http://otro:8000/openapi.json
    python tools/generar_http.py --archivo openapi.json

Regenerar cada vez que cambie una ruta (ver docs/deploy/PLAN.md).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
import urllib.request
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SALIDA = RAIZ / "http" / "api"
LIMITE = "FaroLimite"

# Rutas públicas (sin Authorization).
PUBLICAS = re.compile(r"^(/|/health|/consulta.*|/api/v1/auth/login)$")

# Valores de ejemplo por nombre de campo (datos ficticios, L14 de la constitución).
POR_NOMBRE = {
    "rut": "77777777-7", "rut_emisor": "77777777-7", "rut_empresa": "77777777-7",
    "rut_receptor": "76111111-6", "rut_usuario": "12345678-5", "dv_empresa": "7", "dv_emisor": "7",
    "razon_social": "EMPRESA DEMO SPA", "giro": "Cafetería", "codigo_actividad": 561000,
    "direccion": "Calle Ficticia 123", "comuna": "Santiago", "ciudad": "Santiago",
    "email": "hola@empresademo.cl", "username": "admin", "password": "{{clave}}",
    "nombre": "Servicio de ejemplo", "tipo_dte": 33, "folio": 0, "ambiente": "certificacion",
    "cantidad": 1, "precio_unitario": 10000, "numero_linea": 1, "track_id": 0,
    "receptor.rut": "76111111-6", "receptor.razon_social": "CLIENTE DEMO LTDA", "receptor.giro": "Comercio",
    "receptor.direccion": "Av. Siempreviva 742", "receptor.comuna": "Santiago", "receptor.ciudad": "Santiago",
}
# Archivos de ejemplo para campos multipart (fixtures ficticios del corpus dorado).
ARCHIVO_CAF = "../../tests/dorado/fixtures/caf_33.xml"
ARCHIVO_CERT = "../../tests/dorado/fixtures/cert_demo.p12"


def slug(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")


class Esquemas:
    def __init__(self, spec: dict):
        self.comp = spec.get("components", {}).get("schemas", {})

    def resolver(self, s: dict) -> dict:
        while "$ref" in s:
            s = self.comp[s["$ref"].split("/")[-1]]
        if "allOf" in s and len(s["allOf"]) == 1:
            return self.resolver(s["allOf"][0])
        if "anyOf" in s:
            no_nulos = [x for x in s["anyOf"] if x.get("type") != "null"]
            if no_nulos:
                base = {k: v for k, v in s.items() if k != "anyOf"}
                return {**self.resolver(no_nulos[0]), **base}
        return s

    def ejemplo(self, s: dict, nombre: str = "", prof: int = 0):
        s = self.resolver(s)
        if prof > 6:
            return None
        if "example" in s:
            return s["example"]
        if nombre in POR_NOMBRE:
            return POR_NOMBRE[nombre]
        if "default" in s and s["default"] is not None:
            return s["default"]
        if "enum" in s:
            return s["enum"][0]
        t = s.get("type")
        if t == "object" or "properties" in s:
            # Solo los campos obligatorios: el ejemplo mínimo que el motor acepta. Los opcionales
            # se ven en Swagger o en el esquema; si el objeto no tiene obligatorios, van todos.
            props = s.get("properties", {})
            req = [k for k in props if k in set(s.get("required", []))] or list(props)
            return {k: self.ejemplo(props[k], f"{nombre}.{k}" if nombre == "receptor" else k, prof + 1)
                    for k in req}
        if t == "array":
            return [self.ejemplo(s.get("items", {}), nombre, prof + 1)]
        if t == "integer":
            return 1
        if t == "number":
            return 1000
        if t == "boolean":
            return False
        if s.get("format") == "date":
            return date.today().isoformat()
        if s.get("format") == "binary":
            return "<archivo>"
        return f"<{nombre or 'texto'}>"


def archivo_para(campo: str, ruta: str) -> tuple[str, str]:
    if "caf" in ruta or "caf" in campo:
        return "caf_33.xml", ARCHIVO_CAF
    return "cert_demo.p12", ARCHIVO_CERT


def bloque(metodo: str, ruta: str, op: dict, esq: Esquemas) -> tuple[str, list[str]]:
    variables = []
    url = ruta
    for p in op.get("parameters", []):
        if p["in"] == "path":
            url = url.replace("{" + p["name"] + "}", "{{" + p["name"] + "}}")
            variables.append(p["name"])
    q_req, q_opc = [], []
    for p in op.get("parameters", []):
        if p["in"] != "query":
            continue
        val = esq.ejemplo(p.get("schema", {}), p["name"])
        (q_req if p.get("required") else q_opc).append((p["name"], val))
    lineas = [f"### {op.get('summary') or ruta}"]
    if q_opc:
        lineas.append("# opcionales: " + ", ".join(f"{n}={v}" for n, v in q_opc))
    lineas.append(f"# @name {slug(op.get('operationId') or metodo + ruta).replace('-', '_')[:60]}")
    lineas.append(f"{metodo.upper()} {{{{base}}}}{url}")
    for i, (n, v) in enumerate(q_req):
        lineas.append(f"    {'?' if i == 0 else '&'}{n}={v}")
    if not PUBLICAS.match(ruta):
        lineas.append("Authorization: Bearer {{token}}")
    contenido = op.get("requestBody", {}).get("content", {})
    if "application/json" in contenido:
        cuerpo = esq.ejemplo(contenido["application/json"].get("schema", {}))
        lineas += ["Content-Type: application/json", "", json.dumps(cuerpo, ensure_ascii=False, indent=2)]
    elif "multipart/form-data" in contenido or "application/x-www-form-urlencoded" in contenido:
        tipo = "multipart/form-data" if "multipart/form-data" in contenido else "application/x-www-form-urlencoded"
        s = esq.resolver(contenido[tipo].get("schema", {}))
        todos = s.get("properties", {})
        props = {k: v for k, v in todos.items() if k in set(s.get("required", []))} or todos
        if tipo == "application/x-www-form-urlencoded":
            pares = [f"{k}={esq.ejemplo(v, k)}" for k, v in props.items()]
            lineas += ["Content-Type: application/x-www-form-urlencoded", "", "&".join(pares)]
        else:
            lineas += [f"Content-Type: multipart/form-data; boundary={LIMITE}", ""]
            for k, v in props.items():
                v = esq.resolver(v)
                if v.get("format") == "binary" or v.get("contentMediaType"):
                    nombre, origen = archivo_para(k, ruta)
                    lineas += [f"--{LIMITE}", f'Content-Disposition: form-data; name="{k}"; filename="{nombre}"',
                               "Content-Type: application/octet-stream", "", f"< {origen}"]
                else:
                    valor = "{{clave_cert}}" if k == "password" else esq.ejemplo(v, k)
                    lineas += [f"--{LIMITE}", f'Content-Disposition: form-data; name="{k}"', "", str(valor)]
            lineas.append(f"--{LIMITE}--")
    return "\n".join(lineas), variables


ENCABEZADO = """# {titulo} — Faro API (motor Python)
# GENERADO por tools/generar_http.py desde /openapi.json. No editar a mano: regenerar.
# Requiere la extensión REST Client (humao.rest-client) y el entorno "local" (abajo a la
# derecha en VS Code, o Ctrl+Alt+E). Claves en http/.env (copia http/env.ejemplo).

@clave = {{{{$dotenv FARO_CLAVE}}}}
@clave_cert = {{{{$dotenv FARO_CLAVE_CERT}}}}
{variables}
### Iniciar sesión (ejecuta esto primero; el token dura 60 min)
# @name login
POST {{{{base}}}}/api/v1/auth/login
Content-Type: application/json

{{"username": "{{{{usuario}}}}", "password": "{{{{clave}}}}"}}

###
@token = {{{{login.response.body.access_token}}}}

"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://localhost:8000/openapi.json")
    ap.add_argument("--archivo")
    a = ap.parse_args()
    if a.archivo:
        spec = json.loads(Path(a.archivo).read_text(encoding="utf-8"))
    else:
        with urllib.request.urlopen(a.url, timeout=30) as r:
            spec = json.loads(r.read().decode("utf-8"))
    esq = Esquemas(spec)
    grupos: dict[str, list] = {}
    for ruta, metodos in spec["paths"].items():
        for metodo, op in metodos.items():
            tag = (op.get("tags") or ["Otros"])[0]
            grupos.setdefault(tag, []).append((metodo, ruta, op))
    SALIDA.mkdir(parents=True, exist_ok=True)
    for viejo in SALIDA.glob("*.http"):
        viejo.unlink()
    total = 0
    for i, (tag, ops) in enumerate(grupos.items(), 1):
        bloques, variables = [], []
        for metodo, ruta, op in ops:
            texto, vs = bloque(metodo, ruta, op, esq)
            bloques.append(texto)
            variables += [v for v in vs if v not in variables]
            total += 1
        decl = "".join(f"@{v} = 1\n" for v in variables)
        archivo = SALIDA / f"{i:02d}-{slug(tag)}.http"
        archivo.write_text(ENCABEZADO.format(titulo=tag, variables=decl) + "\n\n".join(bloques) + "\n",
                           encoding="utf-8", newline="\n")
    print(f"{total} operaciones en {len(grupos)} archivos en {SALIDA.relative_to(RAIZ)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
