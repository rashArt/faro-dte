"""
core/servicios/emision.py — Caso de uso "emitir un documento" (fase 2 de docs/deploy/PLAN.md).

Regla del fork: **timbrado = emitido**. Un documento con timbre (TED) ya puede estar en manos
del cliente, así que su folio queda de esa venta para siempre: nunca se reasigna ni se
regenera con otros datos. Solo un folio que nunca llegó a timbrarse vuelve a usarse.

Pasos, en este orden:

1. **Prevalidación sin folio**: RUT, referencias de las notas 56/61 y un *ensayo* completo
   (TED, firma, sobre y XSD con un folio de prueba). Todo lo que el SII rechazaría por forma
   responde 422 aquí, sin gastar folio (hallazgos #5 y #15).
2. **Reserva en una transacción**: el `id_externo` de la venta, el folio y la fila `reservado`
   de `dtes` se escriben juntos, con el candado del CAF (L7). Antes de avanzar el contador se
   reusa el folio de una fila sin timbre (`error`, o `reservado` huérfana).
3. **Generación** con el folio real, fuera de la transacción: TED, firma, sobre, XSD y PDF.
   Los archivos van a `storage/emisiones/<dte_id>/v<intento>/` y nunca se sobrescriben (#4).
4. **Cierre**: la fila pasa a `generado`. Si algo falla en 3 o 4, pasa a `error` con el motivo
   y el folio queda libre para el mismo `id_externo` o la venta siguiente (#6).

Con el mismo `id_externo`: el mismo JSON devuelve el documento existente (reintento, #3); un
JSON distinto se acepta solo si el intento anterior no quedó timbrado; si quedó timbrado, 409.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import threading
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Optional

from core import datos, keystore, reloj
from core.caf import ManejadorCAF
from core.config import settings
from core.crypto import CertificadoDigital
from core.dte import DTEInput
from core.errors import (
    AutorizacionError,
    ConflictoError,
    DTEChileError,
    FolioError,
    SinFoliosError,
    ValidacionError,
)
from core.models import avanzar_folio, bloquear_caf_activo, obtener_caf_activo, registrar_log
from core.orchestrator import OrquestadorDTE, ruta_desde_bd, ruta_para_bd
from core.orchestrator_boleta import TIPOS_BOLETA, OrquestadorBoleta
from core.rut import validar_rut
from core.schema_validator import validar_xml_dte

log = logging.getLogger("dte.emision")

TIPOS_CON_REFERENCIA = (56, 61)


@dataclass(frozen=True)
class Solicitante:
    """Quién pide la emisión. Lo arma la ruta desde el token."""
    origen: str                 # dueño de los `id_externo`: "usuario:<id>" o "apikey:<id>"
    cuenta_id: Optional[int]    # cuenta cuyos negocios y certificados se usan
    es_admin: bool              # solo el administrador elige el folio a mano (#4)


@dataclass(frozen=True)
class _Reserva:
    dte_id: int
    folio: int
    intento: int
    caf_xml: str


class _YaEmitido(Exception):
    """La venta ya tiene su documento timbrado y el JSON es el mismo: se devuelve ese."""

    def __init__(self, dte_id: int):
        super().__init__(dte_id)
        self.dte_id = dte_id


# ---------------------------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------------------------
def emitir(dte_input: DTEInput, solicitante: Solicitante, *, id_externo: Optional[str] = None,
           certificado: Optional[CertificadoDigital] = None) -> dict:
    """Emite (timbra, firma y guarda) un documento. No lo envía al SII.

    Devuelve el mismo diccionario que `core.orchestrator.emitir_documento`, más `estado`,
    `id_externo` y `repetido` (True si es el documento que ya existía para ese `id_externo`).
    """
    tipo = dte_input.tipo_dte.value
    explicito = dte_input.folio if dte_input.folio > 0 else None
    if explicito and not solicitante.es_admin:
        raise AutorizacionError("Solo el administrador puede elegir el folio; omite `folio` o usa 0.",
                                detalle={"folio": explicito})
    huella = _huella(dte_input)

    # Atajo sin candado: el reintento de una venta ya timbrada no paga el ensayo.
    if id_externo:
        emision, dte = _emision_y_documento(datos.transaccion, solicitante.origen, id_externo)
        if emision is not None:
            try:
                _decidir(id_externo, emision, dte, huella, explicito)
            except _YaEmitido as ya:
                return _respuesta(ya.dte_id, id_externo, repetido=True)

    orq = OrquestadorBoleta() if tipo in TIPOS_BOLETA else OrquestadorDTE()
    cert = certificado or _certificado(solicitante, dte_input.emisor.rut, orq)
    _prevalidar(dte_input)
    caf_db = obtener_caf_activo(dte_input.emisor.rut, tipo)
    if not caf_db:
        raise _sin_folios(dte_input.emisor.rut, tipo)
    _ensayar(orq, dte_input, cert, ManejadorCAF(caf_db["caf_xml"].encode("utf-8")), explicito)

    try:
        reserva = _reservar(dte_input, solicitante, id_externo, huella, explicito)
    except _YaEmitido as ya:
        return _respuesta(ya.dte_id, id_externo, repetido=True)

    _generar(orq, dte_input, cert, reserva)
    registrar_log(accion="emitir", tipo_dte=tipo, folio=reserva.folio, rut_emisor=dte_input.emisor.rut,
                  detalle=f"dte_id={reserva.dte_id} intento={reserva.intento}"
                          + (f" id_externo={id_externo}" if id_externo else ""))
    log.info("emitido dte_id=%s tipo=%s folio=%s intento=%s", reserva.dte_id, tipo, reserva.folio,
             reserva.intento)
    return _respuesta(reserva.dte_id, id_externo, repetido=False)


# ---------------------------------------------------------------------------------------------
# 1. Prevalidación (sin folio)
# ---------------------------------------------------------------------------------------------
def _prevalidar(dte_input: DTEInput) -> None:
    errores = []
    for quien, rut in (("emisor", dte_input.emisor.rut), ("receptor", dte_input.receptor.rut)):
        if not validar_rut(rut):
            errores.append(f"El RUT del {quien} no es válido: {rut}")
    if dte_input.tipo_dte.value in TIPOS_CON_REFERENCIA and not dte_input.referencias:
        errores.append("Una nota de crédito o débito (56/61) debe referenciar el documento que "
                       "corrige: falta `referencias`.")
    if errores:
        raise ValidacionError("Los datos del documento no son válidos.", detalle={"errores": errores})


def _ensayar(orq: OrquestadorDTE, dte_input: DTEInput, cert: CertificadoDigital,
             caf: ManejadorCAF, explicito: Optional[int]) -> None:
    """Arma el documento completo con un folio de prueba y lo valida contra el XSD del SII. Nada
    se guarda: si falla, la emisión responde 422 sin haber tocado un folio."""
    ensayo = dte_input.model_copy(deep=True)
    ensayo.folio = explicito or caf.datos.folio_desde
    _, ted = orq._timbrar(ensayo, caf)
    _, xml_envio = orq.generar_documento(ensayo, cert, ted)
    _exigir_esquema(xml_envio)


def _exigir_esquema(xml_envio: bytes) -> None:
    resultado = validar_xml_dte(xml_envio)
    if not resultado.valido:
        raise ValidacionError("El documento no cumple el esquema del SII (XSD).",
                              detalle={"errores": resultado.errores[:20]})


# ---------------------------------------------------------------------------------------------
# 2. Reserva (una transacción, con el candado del CAF)
# ---------------------------------------------------------------------------------------------
def _reservar(dte_input: DTEInput, solicitante: Solicitante, id_externo: Optional[str],
              huella: str, explicito: Optional[int]) -> _Reserva:
    rut, tipo = dte_input.emisor.rut, dte_input.tipo_dte.value
    ahora = _ahora()
    with datos.transaccion(inmediata=True) as con:
        caf = bloquear_caf_activo(con, rut, tipo)
        if not caf:
            raise _sin_folios(rut, tipo)

        emision = propia = None
        if id_externo:
            emision, dte = _emision_y_documento(lambda: _SinCerrar(con), solicitante.origen, id_externo)
            if emision is not None and _decidir(id_externo, emision, dte, huella, explicito) == "reintentar":
                # Su propia fila sin timbre: mismo folio, si sigue siendo del mismo emisor, tipo y CAF.
                if (dte["rut_emisor"], dte["tipo_dte"]) == (rut, tipo) \
                        and caf["folio_desde"] <= dte["folio"] <= caf["folio_hasta"]:
                    propia = dte

        if propia is not None:
            fila, folio = propia, propia["folio"]
        elif explicito:
            fila, folio = _folio_explicito(con, caf, rut, tipo, explicito), explicito
        else:
            fila = con.execute(
                "SELECT id, folio, intento FROM dtes WHERE rut_emisor=? AND tipo_dte=? "
                "AND folio BETWEEN ? AND ? AND (estado='error' OR (estado='reservado' AND actualizado_en < ?)) "
                "ORDER BY folio LIMIT 1",
                (rut, tipo, caf["folio_desde"], caf["folio_hasta"], _vencimiento(ahora)),
            ).fetchone()
            if fila is not None:
                folio = fila["folio"]
            else:
                folio = avanzar_folio(con, caf)
                if folio is None:
                    raise _sin_folios(rut, tipo)

        totales = _totales(dte_input)
        valores = (tipo, folio, rut, dte_input.receptor.rut, dte_input.receptor.razon_social,
                   dte_input.fecha_emision.isoformat(), totales.monto_neto, totales.monto_exento,
                   totales.iva_monto, totales.monto_total)
        if fila is not None:
            dte_id, intento = fila["id"], fila["intento"] + 1
            con.execute(
                "UPDATE dtes SET tipo_dte=?, folio=?, rut_emisor=?, rut_receptor=?, razon_social_receptor=?, "
                "fecha_emision=?, monto_neto=?, monto_exento=?, iva=?, monto_total=?, estado='reservado', "
                "intento=?, track_id=NULL, xml_firmado=NULL, pdf_path=NULL, motivo_error=NULL, ambiente=?, "
                "actualizado_en=? WHERE id=?",
                (*valores, intento, settings.sii_ambiente, ahora, dte_id))
            # Si el folio era de otra venta (su intento falló), esa venta lo pierde: su próximo
            # reintento toma otro folio.
            con.execute("UPDATE emisiones SET dte_id=NULL, actualizado_en=? WHERE dte_id=? AND id<>?",
                        (ahora, dte_id, emision["id"] if emision else 0))
        else:
            intento = 1
            dte_id = con.execute(
                "INSERT INTO dtes (tipo_dte, folio, rut_emisor, rut_receptor, razon_social_receptor, "
                "fecha_emision, monto_neto, monto_exento, iva, monto_total, estado, intento, ambiente, "
                "creado_en, actualizado_en) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'reservado', ?, ?, ?, ?)",
                (*valores, intento, settings.sii_ambiente, ahora, ahora)).lastrowid

        if emision is not None:
            con.execute("UPDATE emisiones SET huella=?, dte_id=?, actualizado_en=? WHERE id=?",
                        (huella, dte_id, ahora, emision["id"]))
        elif id_externo:
            con.execute("INSERT INTO emisiones (origen, id_externo, huella, dte_id, creado_en, actualizado_en) "
                        "VALUES (?, ?, ?, ?, ?, ?)", (solicitante.origen, id_externo, huella, dte_id, ahora, ahora))
        return _Reserva(dte_id=dte_id, folio=folio, intento=intento, caf_xml=caf["caf_xml"])


def _folio_explicito(con, caf, rut: str, tipo: int, folio: int):
    """Folio elegido por el administrador (#4): dentro del CAF y nunca hacia atrás. Devuelve la
    fila sin timbre que lo tenía, si la hay, para reusarla."""
    if not caf["folio_desde"] <= folio <= caf["folio_hasta"]:
        raise FolioError(f"El folio {folio} no está en el rango del CAF activo "
                         f"[{caf['folio_desde']}-{caf['folio_hasta']}].",
                         detalle={"folio": folio, "desde": caf["folio_desde"], "hasta": caf["folio_hasta"]})
    fila = con.execute("SELECT id, folio, intento, estado, actualizado_en FROM dtes "
                       "WHERE rut_emisor=? AND tipo_dte=? AND folio=?", (rut, tipo, folio)).fetchone()
    if fila is not None:
        if not _reusable(fila):
            raise FolioError(f"El folio {folio} ya tiene un documento (estado {fila['estado']}).",
                             detalle={"folio": folio, "dte_id": fila["id"], "estado": fila["estado"]})
        return fila
    if folio < caf["folio_siguiente"]:
        raise FolioError(f"El folio {folio} ya se consumió; el siguiente disponible es "
                         f"{caf['folio_siguiente']}.",
                         detalle={"folio": folio, "folio_siguiente": caf["folio_siguiente"]})
    con.execute("UPDATE cafs SET folio_siguiente=? WHERE id=?", (folio + 1, caf["id"]))
    return None


def _decidir(id_externo: str, emision, dte, huella: str, explicito: Optional[int]) -> str:
    """Qué hacer con una venta que ya llegó antes. Devuelve "nueva" o "reintentar"; levanta
    `_YaEmitido` (mismo JSON, ya timbrado) o `ConflictoError` (en curso, o timbrado y cambiado)."""
    if dte is None:
        return "nueva"           # su folio pasó a otra venta: va por uno nuevo
    if dte["estado"] == "reservado" and not _reusable(dte):
        raise ConflictoError(f"La venta {id_externo} se está emitiendo en este momento; reintenta en unos segundos.",
                             codigo="en_curso", detalle={"dte_id": dte["id"], "folio": dte["folio"]})
    if _reusable(dte):
        if explicito and explicito != dte["folio"]:
            raise FolioError(f"La venta {id_externo} ya tiene el folio {dte['folio']}.",
                             detalle={"dte_id": dte["id"], "folio": dte["folio"]})
        return "reintentar"
    if emision["huella"] == huella:
        raise _YaEmitido(dte["id"])
    como = ("El SII lo rechazó: emite uno nuevo con otro id_externo." if dte["estado"] == "rechazado"
            else "Un documento timbrado no cambia: corrígelo con una nota de crédito o débito.")
    raise ConflictoError(
        f"La venta {id_externo} ya tiene el folio {dte['folio']} timbrado (estado {dte['estado']}). {como}",
        codigo="ya_emitido", detalle={"dte_id": dte["id"], "folio": dte["folio"], "estado": dte["estado"]})


# ---------------------------------------------------------------------------------------------
# 3-4. Generación y cierre
# ---------------------------------------------------------------------------------------------
def _generar(orq: OrquestadorDTE, dte_input: DTEInput, cert: CertificadoDigital, reserva: _Reserva) -> None:
    doc = dte_input.model_copy(deep=True)
    doc.folio = reserva.folio
    try:
        _, ted = orq._timbrar(doc, ManejadorCAF(reserva.caf_xml.encode("utf-8")))
        dte_firmado, xml_envio = orq.generar_documento(doc, cert, ted)
        _exigir_esquema(xml_envio)
        pdf = orq.generar_pdf(doc, ted)
        rutas = _rutas(reserva.dte_id, reserva.intento, doc.tipo_dte.value, reserva.folio)
        rutas["dir"].mkdir(parents=True, exist_ok=True)
        xml_dte = dte_firmado.encode("ISO-8859-1")
        for clave, contenido in (("xml_dte", xml_dte), ("xml_envio", xml_envio), ("pdf", pdf)):
            with open(rutas[clave], "xb") as f:   # "x": nunca sobrescribe
                f.write(contenido)
        with datos.transaccion() as con:
            filas = con.execute(
                "UPDATE dtes SET estado='generado', xml_firmado=?, pdf_path=?, motivo_error=NULL, actualizado_en=? "
                "WHERE id=? AND intento=? AND estado='reservado'",
                (xml_dte.decode("ISO-8859-1"), ruta_para_bd(rutas["pdf"]), _ahora(),
                 reserva.dte_id, reserva.intento)).rowcount
        if filas != 1:
            raise ConflictoError("La reserva del folio venció mientras se generaba el documento; reintenta.",
                                 detalle={"dte_id": reserva.dte_id, "folio": reserva.folio})
    except BaseException as e:
        _marcar_error(reserva, e)
        if isinstance(e, DTEChileError):
            e.detalle = {**(e.detalle or {}), "dte_id": reserva.dte_id, "folio": reserva.folio, "estado": "error"}
            raise
        if not isinstance(e, Exception):
            raise
        log.exception("falló la generación dte_id=%s folio=%s", reserva.dte_id, reserva.folio)
        raise DTEChileError("No se pudo generar el documento. El folio queda reservado para reintentar "
                            "con el mismo id_externo.", codigo="error_emision",
                            detalle={"dte_id": reserva.dte_id, "folio": reserva.folio, "estado": "error"}) from e


def _marcar_error(reserva: _Reserva, e: BaseException) -> None:
    motivo = (e.mensaje if isinstance(e, DTEChileError) else f"{type(e).__name__}: {e}")[:500]
    try:
        with datos.transaccion() as con:
            con.execute("UPDATE dtes SET estado='error', motivo_error=?, actualizado_en=? "
                        "WHERE id=? AND intento=? AND estado='reservado'",
                        (motivo, _ahora(), reserva.dte_id, reserva.intento))
    except Exception:   # la base no responde: la fila queda `reservado` y vence sola
        log.exception("no se pudo marcar en error dte_id=%s", reserva.dte_id)


def _respuesta(dte_id: int, id_externo: Optional[str], *, repetido: bool) -> dict:
    with datos.transaccion() as con:
        d = con.execute("SELECT id, tipo_dte, folio, monto_total, estado, intento, ambiente, pdf_path "
                        "FROM dtes WHERE id=?", (dte_id,)).fetchone()
    rutas = _rutas(d["id"], d["intento"], d["tipo_dte"], d["folio"])
    envio = rutas["xml_envio"]
    pdf = ruta_desde_bd(d["pdf_path"]) or rutas["pdf"]
    nombre = "Boleta" if d["tipo_dte"] in TIPOS_BOLETA else "DTE"
    return {
        "dte_id": d["id"],
        "folio": d["folio"],
        "tipo_dte": d["tipo_dte"],
        "monto_total": d["monto_total"],
        "estado": d["estado"],
        "id_externo": id_externo,
        "repetido": repetido,
        "xml_dte_path": str(rutas["xml_dte"]),
        "pdf_path": str(pdf),
        "xml_envio_path": str(envio),
        "xml_envio_b64": base64.b64encode(envio.read_bytes()).decode("ascii") if envio.exists() else "",
        "ambiente": d["ambiente"],
        "mensaje": (f"{nombre} ya emitido para {id_externo}. Folio {d['folio']}." if repetido
                    else f"{nombre} emitido. Folio {d['folio']}."),
    }


# ---------------------------------------------------------------------------------------------
# Apoyo
# ---------------------------------------------------------------------------------------------
_certificados: dict[tuple[int, str], CertificadoDigital] = {}
_candado_certificados = threading.Lock()


def _certificado(solicitante: Solicitante, rut_emisor: str, orq: OrquestadorDTE) -> CertificadoDigital:
    """El certificado del negocio del emisor (keystore, cifrado, L6). Se descifra una vez por
    proceso y se vuelve a descifrar si se reemplaza (cambia `creado`). Sin negocio con
    certificado, el de la configuración (`CERTIFICADO_PATH`), como la emisión de siempre."""
    fila = None
    if solicitante.cuenta_id is not None:
        with datos.transaccion() as con:
            fila = con.execute(
                "SELECT c.id, c.creado FROM negocios n JOIN certificados c ON c.id = n.cert_id "
                "WHERE n.cuenta_id=? AND n.rut=? AND c.cuenta_id=?",
                (solicitante.cuenta_id, rut_emisor, solicitante.cuenta_id)).fetchone()
    if fila is None:
        return orq._obtener_certificado(None)
    clave = (fila["id"], fila["creado"])
    with _candado_certificados:
        if clave not in _certificados:
            for vieja in [k for k in _certificados if k[0] == fila["id"]]:
                del _certificados[vieja]
            _certificados[clave] = keystore.cargar_certificado(fila["id"], solicitante.cuenta_id)
        return _certificados[clave]


def _emision_y_documento(abrir, origen: str, id_externo: str):
    with abrir() as con:
        emision = con.execute("SELECT id, huella, dte_id FROM emisiones WHERE origen=? AND id_externo=?",
                              (origen, id_externo)).fetchone()
        dte = None
        if emision is not None and emision["dte_id"] is not None:
            dte = con.execute("SELECT id, rut_emisor, tipo_dte, folio, estado, intento, actualizado_en "
                              "FROM dtes WHERE id=?", (emision["dte_id"],)).fetchone()
    return emision, dte


class _SinCerrar:
    """Usa una conexión ya abierta (la de la transacción de reserva) como `with`."""

    def __init__(self, con):
        self.con = con

    def __enter__(self):
        return self.con

    def __exit__(self, *_):
        return False


def _reusable(dte) -> bool:
    """Una fila sin timbre cuyo folio puede volver a usarse: `error`, o `reservado` vencida."""
    if dte["estado"] == "error":
        return True
    return dte["estado"] == "reservado" and dte["actualizado_en"] < _vencimiento(_ahora())


def _huella(dte_input: DTEInput) -> str:
    """Huella del JSON tal como llegó (sin los valores por defecto): distingue reintento de corrección."""
    datos_json = dte_input.model_dump(mode="json", exclude_unset=True, exclude={"folio"})
    texto = json.dumps(datos_json, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def _totales(dte_input: DTEInput):
    from core.dte import calcular_totales
    return calcular_totales(dte_input.items, dte_input.tipo_dte)


def _rutas(dte_id: int, intento: int, tipo: int, folio: int) -> dict:
    carpeta = Path(settings.storage_path) / "emisiones" / str(dte_id) / f"v{intento}"
    doc, sobre = ("BOLETA", "EnvioBOLETA") if tipo in TIPOS_BOLETA else ("DTE", "EnvioDTE")
    return {"dir": carpeta,
            "xml_dte": carpeta / f"{doc}_{tipo}_{folio}.xml",
            "xml_envio": carpeta / f"{sobre}_{tipo}_{folio}.xml",
            "pdf": carpeta / f"{doc}_{tipo}_{folio}.pdf"}


def _sin_folios(rut: str, tipo: int) -> SinFoliosError:
    return SinFoliosError(
        f"No hay folios disponibles para el emisor {rut} y tipo DTE {tipo}. "
        "Verifique que el CAF esté cargado y activo.",
        detalle={"rut_emisor": rut, "tipo_dte": tipo})


def _ahora() -> str:
    return reloj.ahora().isoformat(timespec="microseconds")


def _vencimiento(ahora: str) -> str:
    from datetime import datetime
    limite = datetime.fromisoformat(ahora) - timedelta(minutes=settings.emision_plazo_reserva_min)
    return limite.isoformat(timespec="microseconds")
