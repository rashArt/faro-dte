"""
core/empresa.py — La empresa de la instancia (docs/deploy/PLAN.md, fase 7).

Una instancia de Faro atiende a **una sola empresa** (un RUT). Aquí vive lo que la API expone
en `/api/v1/empresa`: sus datos, su certificado, su ambiente y el cambio de ambiente.

La empresa es la fila de la tabla `negocios` (el nombre que le da el proyecto original, que
admite varias por cuenta). Las rutas antiguas `/api/v1/keystore/negocios/*` siguen existiendo
para la interfaz heredada; una segunda empresa se rechaza en los dos caminos.

El **ambiente** (demo, certificación o producción) es de la empresa, no del `.env`:
`SII_AMBIENTE` solo es el valor con que nace una empresa nueva. Cambiarlo es una acción
explícita del administrador, con requisitos, y queda en la auditoría (L1 de
docs/CONSTITUCION.md). En demo (`simulacion`) nada sale al SII ni se le consulta el RUT: sirve
para aprender la API con datos ficticios. El RUT se valida en el SII al pasar a certificación.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import List, Optional

from core import database, keystore, negocios
from core.ambiente import AMBIENTES, CERTIFICACION, PRODUCCION, SIMULACION, ambiente_actual
from core.config import settings
from core.errors import ConflictoError, ValidacionError
from core.models import listar_cafs, caf_utilizable, registrar_log
from core.monitoreo import resumen_salud

logger = logging.getLogger("dte.empresa")

# Lo que el SII exige en el emisor de cada documento.
CAMPOS_EMISOR = ("razon_social", "giro", "codigo_actividad", "direccion", "comuna", "ciudad")
_COLUMNAS = ("id, cuenta_id, rut, razon_social, cert_id, ambiente, creado, giro, codigo_actividad, "
             "direccion, comuna, ciudad, email, telefono")


def _rut(rut: Optional[str]) -> str:
    return (rut or "").replace(".", "").strip().upper()


def obtener() -> Optional[dict]:
    """La empresa de la instancia, o None si todavía no se crea."""
    fila = database.obtenerUno(f"SELECT {_COLUMNAS} FROM negocios ORDER BY id LIMIT 1")
    return dict(fila) if fila else None


def exigir() -> dict:
    empresa = obtener()
    if empresa is None:
        raise ConflictoError("La instancia todavía no tiene empresa: créala con PUT /api/v1/empresa.",
                             codigo="sin_empresa")
    return empresa


def exigir_unica(rut: str) -> None:
    """Una instancia, una empresa: otro RUT se rechaza."""
    empresa = obtener()
    if empresa is not None and _rut(rut) != _rut(empresa["rut"]):
        raise ConflictoError(
            f"Esta instancia ya es de la empresa {empresa['rut']}: no admite otra. "
            "Cada empresa tiene su propia instancia de Faro.",
            codigo="empresa_unica", detalle={"rut": empresa["rut"]})


# ---------------------------------------------------------------------------------------------
# Datos y certificado
# ---------------------------------------------------------------------------------------------
def guardar(cuenta_id: int, rut: Optional[str], datos: dict) -> dict:
    """Crea la empresa (con su RUT) o actualiza sus datos. Devuelve la empresa. En modo demo
    (`simulacion`) no se consulta al SII; en certificación o producción el RUT debe estar en el
    registro del SII, que además entrega la razón social."""
    empresa = obtener()
    if empresa is None:
        if not rut:
            raise ValidacionError("Para crear la empresa hace falta su `rut`.")
        try:
            creada = negocios.agregar_negocio(cuenta_id, rut, razon_social=datos.get("razon_social"))
        except ValueError as e:
            raise ValidacionError(str(e)) from e
        empresa = {"id": creada["id"], "cuenta_id": cuenta_id}
    elif rut:
        exigir_unica(rut)
    negocios.actualizar_datos(empresa["cuenta_id"], empresa["id"], datos)
    return obtener()


def guardar_certificado(p12: bytes, password: str, nombre: Optional[str] = None) -> dict:
    """Guarda cifrado (L6) el certificado con que firma la empresa. Devuelve sus metadatos."""
    from core.errors import CertificadoError
    empresa = exigir()
    try:
        info = keystore.guardar_certificado(empresa["cuenta_id"], p12, password, nombre=nombre)
    except Exception as e:  # noqa: BLE001 — contraseña incorrecta o archivo que no es un .p12
        raise CertificadoError(f"Certificado inválido o contraseña incorrecta: {e}") from e
    with database.get_db() as conn:
        conn.execute("UPDATE negocios SET cert_id=? WHERE id=?", (info["id"], empresa["id"]))
    return certificado(obtener())


def certificado(empresa: Optional[dict]) -> Optional[dict]:
    """Metadatos del certificado de la empresa (sin secretos) y si sigue vigente."""
    if not empresa or not empresa.get("cert_id"):
        return None
    fila = database.obtenerUno("SELECT id, rut, nombre, vencimiento, creado FROM certificados WHERE id=?",
                               (empresa["cert_id"],))
    if not fila:
        return None
    cert = dict(fila)
    try:
        vence = datetime.fromisoformat(cert["vencimiento"])
        if vence.tzinfo is None:
            vence = vence.replace(tzinfo=timezone.utc)
        cert["vigente"] = vence > datetime.now(timezone.utc)
    except (TypeError, ValueError):
        cert["vigente"] = True
    return cert


# ---------------------------------------------------------------------------------------------
# Folios
# ---------------------------------------------------------------------------------------------
def folios(ambiente: Optional[str] = None) -> dict:
    """Folios de la empresa por tipo de documento en `ambiente` (por defecto, el actual): cuántos
    quedan, de qué CAF sale el próximo y cuándo vence cada CAF."""
    empresa = exigir()
    ambiente = ambiente or ambiente_actual()
    salud = resumen_salud(empresa["rut"], ambiente=ambiente)
    tipos: dict[int, dict] = {}
    for caf in salud["cafs"]:
        t = tipos.setdefault(caf["tipo_dte"], {"tipo_dte": caf["tipo_dte"], "disponibles": 0, "cafs": []})
        if caf["estado"] != "vencido":
            t["disponibles"] += caf["restantes"]
        t["cafs"].append(caf)
    return {"rut": empresa["rut"], "ambiente": ambiente, "estado": salud["verdict"],
            "tipos": [tipos[k] for k in sorted(tipos)]}


# ---------------------------------------------------------------------------------------------
# Ambiente
# ---------------------------------------------------------------------------------------------
def requisitos_produccion(empresa: Optional[dict], *, consultar_sii: bool) -> List[dict]:
    """Lo que falta para pasar a producción (lista vacía: se puede). `consultar_sii` agrega, cuando
    lo demás ya se cumple, la comprobación en línea de que el SII autorizó a la empresa."""
    if empresa is None:
        return [{"codigo": "sin_empresa", "detalle": "Crea la empresa con PUT /api/v1/empresa."}]
    falta: List[dict] = []
    vacios = [c for c in CAMPOS_EMISOR if empresa.get(c) in (None, "")]
    if vacios:
        falta.append({"codigo": "datos_empresa",
                      "detalle": f"Faltan datos de la empresa: {', '.join(vacios)} (PUT /api/v1/empresa)."})
    cert = certificado(empresa)
    if cert is None:
        falta.append({"codigo": "certificado",
                      "detalle": "Falta el certificado digital (PUT /api/v1/empresa/certificado)."})
    elif not cert["vigente"]:
        falta.append({"codigo": "certificado", "detalle": f"El certificado venció el {cert['vencimiento'][:10]}."})
    if not any(caf_utilizable(c) for c in listar_cafs(rut_emisor=empresa["rut"], ambiente=PRODUCCION)):
        falta.append({"codigo": "caf_produccion",
                      "detalle": "No hay un CAF de producción vigente y con folios "
                                 "(POST /api/v1/caf con ambiente=produccion)."})
    for problema in settings.problemas_seguridad():
        falta.append({"codigo": "seguridad", "detalle": problema})
    if consultar_sii and not falta:   # al SII solo se le pregunta cuando lo local ya está en regla
        falta += _autorizacion_sii(empresa["rut"])
    return falta


def _autorizacion_sii(rut: str) -> List[dict]:
    """El SII solo recibe documentos de producción de una empresa que ya autorizó (resolución).
    Si la consulta falla no se asume nada: el cambio no se hace."""
    try:
        info = negocios.info_empresa(rut, PRODUCCION)
    except Exception as e:  # noqa: BLE001 — el SII no respondió
        return [{"codigo": "autorizacion_sii",
                 "detalle": f"No se pudo consultar al SII si la empresa está autorizada en producción ({e}). "
                            "Reintenta en unos minutos."}]
    if not info or not info.get("autorizado"):
        return [{"codigo": "autorizacion_sii",
                 "detalle": f"El SII no muestra a {rut} autorizada para emitir en producción. "
                            "Termina la certificación (docs/CERTIFICACION.md) antes de cambiar."}]
    return []


def requisitos_certificacion(empresa: dict) -> List[dict]:
    """Lo que falta para salir del modo demo a certificación: que el SII conozca el RUT. En demo
    la empresa se crea sin consultarlo; aquí se valida, antes del primer contacto con el SII."""
    rut = empresa["rut"]
    try:
        info = negocios.info_empresa(rut, CERTIFICACION)
    except Exception as e:  # noqa: BLE001 — el SII no respondió
        return [{"codigo": "rut_sii",
                 "detalle": f"No se pudo consultar el RUT en el SII ({e}). Reintenta en unos minutos."}]
    if not info:
        return [{"codigo": "rut_sii",
                 "detalle": f"El SII no muestra a {rut} en su ambiente de certificación. Inscribe la empresa "
                            "en certificación en el SII (docs/CERTIFICACION.md) antes de cambiar."}]
    _completar_razon_social(rut, info)
    return []


def _completar_razon_social(rut: str, info: dict) -> None:
    """Una empresa creada en demo puede no tener razón social: se toma la del registro del SII."""
    if info.get("razon_social"):
        with database.get_db() as conn:
            conn.execute("UPDATE negocios SET razon_social=? WHERE rut=? AND "
                         "(razon_social IS NULL OR razon_social='')", (info["razon_social"], rut))


def cambiar_ambiente(nuevo: str, confirmar_rut: str, actor: Optional[str]) -> dict:
    """Cambia el ambiente de la empresa. Pide repetir el RUT (no se cambia por accidente), valida
    el RUT en el SII al salir del modo demo, exige los requisitos para producción y deja el
    cambio en la auditoría. Volver a demo o a certificación no pide nada. Los documentos y CAF
    de cada ambiente quedan como están: cada uno guarda el ambiente en que nació."""
    empresa = exigir()
    nuevo = (nuevo or "").strip().lower()
    if nuevo not in AMBIENTES:
        raise ValidacionError("`ambiente` debe ser simulacion, certificacion o produccion.",
                              detalle={"ambiente": nuevo})
    if _rut(confirmar_rut) != _rut(empresa["rut"]):
        raise ValidacionError("`confirmar_rut` no es el RUT de la empresa: el ambiente no se cambió.")
    anterior = empresa["ambiente"]
    if nuevo == anterior:
        return {"ambiente": nuevo, "anterior": anterior, "cambiado": False}
    if nuevo == PRODUCCION:
        falta = requisitos_produccion(empresa, consultar_sii=True)
        if falta:
            raise ConflictoError("La empresa todavía no puede pasar a producción.",
                                 codigo="requisitos_produccion", detalle={"falta": falta})
    elif nuevo == CERTIFICACION and anterior == SIMULACION:
        falta = requisitos_certificacion(empresa)
        if falta:
            raise ConflictoError("La empresa todavía no puede pasar a certificación.",
                                 codigo="requisitos_certificacion", detalle={"falta": falta})
    with database.get_db() as conn:
        conn.execute("UPDATE negocios SET ambiente=? WHERE id=?", (nuevo, empresa["id"]))
    registrar_log("cambio_ambiente", rut_emisor=empresa["rut"], actor=actor,
                  detalle=json.dumps({"de": anterior, "a": nuevo}))
    logger.warning("AMBIENTE de %s: %s -> %s (por %s)", empresa["rut"], anterior, nuevo, actor)
    try:   # la resolución de la carátula es otra en cada ambiente; si falla, se consulta al emitir
        from core.resolucion import resolucion_emisor
        resolucion_emisor(empresa["rut"], nuevo, refrescar=True)
    except Exception:  # noqa: BLE001
        pass
    return {"ambiente": nuevo, "anterior": anterior, "cambiado": True}


# ---------------------------------------------------------------------------------------------
# Lo que responde GET /api/v1/empresa
# ---------------------------------------------------------------------------------------------
def estado(*, consultar_sii: bool = False) -> dict:
    empresa = obtener()
    if empresa is None:
        return {"configurada": False, "ambiente": ambiente_actual(), "certificado": None,
                "produccion": {"puede": False, "falta": requisitos_produccion(None, consultar_sii=False)}}
    falta = requisitos_produccion(empresa, consultar_sii=consultar_sii)
    datos = {c: empresa.get(c) for c in ("rut", *CAMPOS_EMISOR, "email", "telefono")}
    return {
        "configurada": True, **datos, "ambiente": empresa["ambiente"],
        "certificado": certificado(empresa),
        "produccion": {
            "puede": not falta, "falta": falta,
            # Sin `consultar_sii` no se pregunta al SII: se comprueba al hacer el cambio.
            "autorizacion_sii": "consultada" if consultar_sii else "se comprueba al cambiar de ambiente",
        },
    }
