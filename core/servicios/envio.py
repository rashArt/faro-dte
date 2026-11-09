"""
core/servicios/envio.py — Despacho al SII: enviar un documento y seguir su veredicto (fase 5 de
docs/deploy/PLAN.md).

Ciclo de un documento con `seguimiento` (emitir con `enviar`, o un envío a mano):

    generado ──envío──► enviado ──veredicto──► aceptado | aceptado_con_reparos | rechazado
        │                  │
        │                  └── sin veredicto tras VEREDICTO_ALERTA_HORAS ──► sin_veredicto
        │                      (se sigue consultando; si llega, pasa a su veredicto)
        └── ENVIO_MAX_INTENTOS fallos seguidos ──► envio_fallido (POST /dte/{id}/enviar reintenta)

- Se envía **el sobre guardado al emitir** (`storage/emisiones/<id>/v<intento>/`), tal cual: lo
  firmado no se vuelve a armar (L1). Facturas y notas por SOAP (`ClienteFacturas`), boletas por
  REST (`ClienteBoletas`), cada una con su token (L9). Un documento por sobre.
- El veredicto sale de `ACEPTADOS`/`RECHAZADOS`/`REPAROS` del documento, **nunca de `EPR`**, que
  solo dice que el sobre se procesó (L3). El código de rechazo de una factura llega solo por
  correo del SII; el de una boleta viene en la consulta.
- Un rechazo quema el folio: el documento no se reenvía. Se corrige con otro documento (otro
  `id_externo`) y el folio se informa para anular (`GET /dte/alertas`).
- Varios procesos pueden correr el despacho: cada documento se toma con `tomado_hasta` (un UPDATE
  condicional) y nadie más lo trabaja hasta que vence.
- Con `ENVIO_SII=desactivado` nada sale al SII: emitir funciona igual y el documento queda
  `generado`.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from core import datos, reloj
from core.config import settings
from core.errors import ConflictoError, RecursoNoEncontrado, SIIError
from core.models import registrar_log
from core.orchestrator_boleta import TIPOS_BOLETA

log = logging.getLogger("dte.envio")

GENERADO = "generado"
ENVIADO = "enviado"
ACEPTADO = "aceptado"
CON_REPAROS = "aceptado_con_reparos"
RECHAZADO = "rechazado"
ENVIO_FALLIDO = "envio_fallido"
SIN_VEREDICTO = "sin_veredicto"

POR_ENVIAR = (GENERADO, ENVIO_FALLIDO)
POR_CONSULTAR = (ENVIADO, SIN_VEREDICTO)
FINALES = (ACEPTADO, CON_REPAROS, RECHAZADO)

# Estados del sobre de factura que ya son rechazo sin desglose por documento (core/seguimiento.py).
_SOBRE_RECHAZADO = {"RCH", "RSC", "RFR", "RCT"}
# Espera antes de cada consulta del veredicto, según cuántas van (la última se repite).
_ESPERAS_CONSULTA = (timedelta(seconds=20), timedelta(minutes=1), timedelta(minutes=5),
                     timedelta(minutes=15), timedelta(minutes=30), timedelta(hours=1))
# Pausa mínima entre consultas pedidas a mano (`GET /dte/{id}/estado?consultar_sii=true`).
_CONSULTA_MINIMA = timedelta(seconds=30)
# Cuánto dura la toma de un documento por un proceso (más que un envío lento con reintentos).
_TOMA = timedelta(minutes=5)

_COLUMNAS = ("id, tipo_dte, folio, rut_emisor, estado, track_id, intento, origen, seguimiento, "
             "intentos_envio, proxima_accion, enviado_en, consultado_en, veredicto_en, motivo_sii, ambiente")


def activo() -> bool:
    return settings.envio_sii == "activo"


# ---------------------------------------------------------------------------------------------
# Entradas
# ---------------------------------------------------------------------------------------------
def programar(dte_id: int) -> str:
    """Deja el documento para que el despacho lo envíe y siga su veredicto. Devuelve qué pasó
    con el envío: `programado`, `desactivado` (ENVIO_SII) o el estado si ya no está por enviar."""
    if not activo():
        return "desactivado"
    with datos.transaccion() as con:
        filas = con.execute(
            "UPDATE dtes SET seguimiento=1, proxima_accion=COALESCE(proxima_accion, ?), actualizado_en=? "
            "WHERE id=? AND estado IN (?, ?)", (_ahora(), _ahora(), dte_id, *POR_ENVIAR)).rowcount
        if filas:
            return "programado"
        fila = con.execute("SELECT estado FROM dtes WHERE id=?", (dte_id,)).fetchone()
    return fila["estado"] if fila else "no_encontrado"


def enviar_ahora(dte_id: int) -> dict:
    """Envía ya (`POST /dte/{id}/enviar`). Un documento ya enviado no se reenvía: devuelve su
    estado. Lo deja con `seguimiento`, así el despacho sigue su veredicto."""
    if not activo():
        raise ConflictoError("El envío al SII está desactivado en esta instancia (ENVIO_SII).",
                             codigo="envio_desactivado")
    doc = _documento(dte_id)
    if doc["estado"] not in POR_ENVIAR:
        return estado(dte_id)
    if not _tomar(dte_id):
        raise ConflictoError(f"El documento {dte_id} se está enviando en este momento; reintenta en unos segundos.",
                             codigo="en_curso", detalle={"dte_id": dte_id})
    try:
        # Un envío a mano de un `envio_fallido` parte de cero: si vuelve a fallar, el despacho
        # lo reintenta otra vez con espera creciente.
        with datos.transaccion() as con:
            con.execute("UPDATE dtes SET estado=?, intentos_envio=0 WHERE id=? AND estado=?",
                        (GENERADO, dte_id, ENVIO_FALLIDO))
        _enviar(_documento(dte_id), manual=True)
    finally:
        _soltar(dte_id)
    return estado(dte_id)


def estado(dte_id: int, *, consultar_sii: bool = False) -> dict:
    """El estado guardado del documento. Con `consultar_sii`, si espera veredicto y pasaron 30
    segundos desde la última consulta, pregunta al SII antes de responder."""
    doc = _documento(dte_id)
    if consultar_sii and activo() and doc["estado"] in POR_CONSULTAR and doc["track_id"]:
        ultima = doc["consultado_en"]
        if not ultima or datetime.fromisoformat(ultima) <= reloj.ahora() - _CONSULTA_MINIMA:
            if _tomar(dte_id):
                try:
                    _consultar(_documento(dte_id))
                finally:
                    _soltar(dte_id)
            doc = _documento(dte_id)
    return _vista(doc)


def alertas() -> dict:
    """Lo que necesita a una persona: documentos sin veredicto, envíos que agotaron los
    intentos y folios rechazados por el SII (no se reenvían; se informan para anular)."""
    with datos.transaccion() as con:
        def de(est):
            return [_vista(f) for f in con.execute(
                f"SELECT {_COLUMNAS} FROM dtes WHERE estado=? ORDER BY id", (est,)).fetchall()]
        return {"sin_veredicto": de(SIN_VEREDICTO), "envio_fallido": de(ENVIO_FALLIDO),
                "folios_por_anular": de(RECHAZADO)}


# ---------------------------------------------------------------------------------------------
# Despacho en segundo plano
# ---------------------------------------------------------------------------------------------
def ciclo(limite: int = 20) -> dict:
    """Una pasada: envía lo pendiente y consulta los veredictos que tocan. Devuelve cuántos envíos
    y consultas intentó (con o sin éxito). Cada documento lo trabaja un solo proceso a la vez."""
    if not activo():
        return {"envios": 0, "consultas": 0}
    ahora = _ahora()
    with datos.transaccion() as con:
        por_enviar = [f["id"] for f in con.execute(
            "SELECT id FROM dtes WHERE seguimiento=1 AND estado=? AND proxima_accion<=? "
            "ORDER BY proxima_accion LIMIT ?", (GENERADO, ahora, limite)).fetchall()]
        por_consultar = [f["id"] for f in con.execute(
            "SELECT id FROM dtes WHERE seguimiento=1 AND estado IN (?, ?) AND proxima_accion<=? "
            "ORDER BY proxima_accion LIMIT ?", (*POR_CONSULTAR, ahora, limite)).fetchall()]
    hechos = {"envios": 0, "consultas": 0}
    for clave, ids, paso in (("envios", por_enviar, _enviar), ("consultas", por_consultar, _consultar)):
        for dte_id in ids:
            if not _tomar(dte_id):
                continue
            try:
                paso(_documento(dte_id))
                hechos[clave] += 1
            except Exception:  # noqa: BLE001 — un documento no detiene el despacho
                log.exception("despacho: falló el documento %s", dte_id)
            finally:
                _soltar(dte_id)
    return hechos


class ProgramadorDespacho:
    """Tarea asyncio del lifespan, como el programador del RVD (`core/scheduler.py`): sin cron,
    igual en Windows, macOS y Linux. El trabajo (base y SII) va a un hilo."""

    def __init__(self, intervalo_seg: int = 15) -> None:
        self.intervalo_seg = intervalo_seg
        self._tarea: Optional[asyncio.Task] = None

    async def _bucle(self) -> None:
        while True:
            try:
                hechos = await asyncio.to_thread(ciclo)
                if any(hechos.values()):
                    log.info("despacho: %s", hechos)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — el bucle nunca muere
                log.exception("despacho: el ciclo falló; se reintenta luego")
            await asyncio.sleep(self.intervalo_seg)

    def iniciar(self) -> None:
        if self._tarea is None or self._tarea.done():
            self._tarea = asyncio.create_task(self._bucle(), name="despacho-sii")
            log.info("Despacho al SII iniciado (cada %ds, ambiente %s)", self.intervalo_seg, settings.sii_ambiente)

    async def detener(self) -> None:
        if self._tarea is None:
            return
        self._tarea.cancel()
        try:
            await self._tarea
        except asyncio.CancelledError:
            pass
        self._tarea = None


programador_despacho = ProgramadorDespacho()


# ---------------------------------------------------------------------------------------------
# Pasos
# ---------------------------------------------------------------------------------------------
def _enviar(doc, *, manual: bool = False) -> None:
    """Envía el sobre guardado. Éxito: `enviado` con TrackID. Falla: reintento con espera
    creciente; al tope, `envio_fallido`. Un envío a mano que falla responde el error."""
    if doc["estado"] not in POR_ENVIAR:
        return
    rut, dv = doc["rut_emisor"].split("-")
    try:
        sobre = _sobre(doc)
        with _cliente(doc) as cliente:
            try:
                if doc["tipo_dte"] in TIPOS_BOLETA:
                    track_id, _ = cliente.enviar_boletas(sobre, rut, dv)
                else:
                    track_id, _ = cliente.enviar_dte(sobre, rut, dv, tipo_dte=doc["tipo_dte"])
            except Exception:
                cliente.olvidar_token()   # si fue el token, el próximo intento pide otro
                raise
    except Exception as e:  # noqa: BLE001 — se guarda el motivo y se reintenta
        motivo = _motivo_error(e)
        intentos = doc["intentos_envio"] + 1
        agotado = intentos >= settings.envio_max_intentos
        espera = timedelta(minutes=min(60, 2 ** (intentos - 1)))
        with datos.transaccion() as con:
            con.execute("UPDATE dtes SET estado=?, intentos_envio=?, motivo_sii=?, proxima_accion=?, "
                        "seguimiento=1, actualizado_en=? WHERE id=?",
                        (ENVIO_FALLIDO if agotado else doc["estado"], intentos, motivo,
                         None if agotado else _iso(reloj.ahora() + espera), _ahora(), doc["id"]))
        log.warning("envío fallido dte_id=%s intento=%s: %s", doc["id"], intentos, motivo)
        if agotado:
            registrar_log(accion="envio_fallido", tipo_dte=doc["tipo_dte"], folio=doc["folio"],
                          rut_emisor=doc["rut_emisor"], detalle=f"dte_id={doc['id']} {motivo}"[:500])
        if manual:
            raise SIIError(f"No se pudo enviar el documento {doc['id']} al SII: {motivo}",
                           detalle={"dte_id": doc["id"], "intentos_envio": intentos}) from e
        return
    ahora = _ahora()
    with datos.transaccion() as con:
        con.execute("UPDATE dtes SET estado=?, track_id=?, enviado_en=?, intentos_envio=0, motivo_sii=NULL, "
                    "seguimiento=1, proxima_accion=?, actualizado_en=? WHERE id=?",
                    (ENVIADO, int(track_id), ahora, _iso(reloj.ahora() + _ESPERAS_CONSULTA[0]), ahora, doc["id"]))
    registrar_log(accion="enviar", tipo_dte=doc["tipo_dte"], folio=doc["folio"], rut_emisor=doc["rut_emisor"],
                  detalle=f"dte_id={doc['id']} track_id={track_id}")
    log.info("enviado dte_id=%s track_id=%s", doc["id"], track_id)


def _consultar(doc) -> None:
    """Pregunta el veredicto del TrackID. Sin veredicto, vuelve a preguntar más tarde; pasado
    `VEREDICTO_ALERTA_HORAS`, el documento queda `sin_veredicto` (se sigue preguntando)."""
    if doc["estado"] not in POR_CONSULTAR or not doc["track_id"]:
        return
    rut, dv = doc["rut_emisor"].split("-")
    ahora = _ahora()
    try:
        with _cliente(doc) as cliente:
            try:
                r = _respuesta_sii(cliente, doc, rut, dv)
            except Exception:
                cliente.olvidar_token()
                raise
    except Exception as e:  # noqa: BLE001 — el SII no respondió: se vuelve a preguntar
        _reprogramar(doc, ahora, motivo=_motivo_error(e))
        return
    veredicto, motivo = _veredicto(doc["tipo_dte"], r)
    if veredicto is None:
        _reprogramar(doc, ahora, motivo=motivo)
        return
    with datos.transaccion() as con:
        con.execute("UPDATE dtes SET estado=?, motivo_sii=?, veredicto_en=?, consultado_en=?, proxima_accion=NULL, "
                    "actualizado_en=? WHERE id=?", (veredicto, motivo, ahora, ahora, ahora, doc["id"]))
    registrar_log(accion=f"veredicto_{veredicto}", tipo_dte=doc["tipo_dte"], folio=doc["folio"],
                  rut_emisor=doc["rut_emisor"], detalle=f"dte_id={doc['id']} track_id={doc['track_id']}")
    log.info("veredicto dte_id=%s: %s", doc["id"], veredicto)


def _reprogramar(doc, ahora: str, *, motivo: Optional[str]) -> None:
    enviado = datetime.fromisoformat(doc["enviado_en"]) if doc["enviado_en"] else reloj.ahora()
    transcurrido = reloj.ahora() - enviado
    vencido = transcurrido >= timedelta(hours=settings.veredicto_alerta_horas)
    nuevo = SIN_VEREDICTO if vencido else doc["estado"]
    paso = sum(1 for e in _ESPERAS_CONSULTA if e <= transcurrido)
    espera = _ESPERAS_CONSULTA[min(paso, len(_ESPERAS_CONSULTA) - 1)]
    with datos.transaccion() as con:
        con.execute("UPDATE dtes SET estado=?, motivo_sii=?, consultado_en=?, proxima_accion=?, actualizado_en=? "
                    "WHERE id=?", (nuevo, motivo, ahora, _iso(reloj.ahora() + espera), ahora, doc["id"]))
    if nuevo == SIN_VEREDICTO and doc["estado"] != SIN_VEREDICTO:
        log.warning("sin veredicto dte_id=%s track_id=%s tras %s", doc["id"], doc["track_id"], transcurrido)
        registrar_log(accion="sin_veredicto", tipo_dte=doc["tipo_dte"], folio=doc["folio"],
                      rut_emisor=doc["rut_emisor"], detalle=f"dte_id={doc['id']} track_id={doc['track_id']}")


def _respuesta_sii(cliente, doc, rut: str, dv: str) -> dict:
    if doc["tipo_dte"] in TIPOS_BOLETA:
        return cliente.consultar_estado(str(doc["track_id"]), rut, dv)
    from core.seguimiento import estado_detallado
    return estado_detallado(cliente, int(doc["track_id"]), rut, dv)


def _veredicto(tipo: int, r: dict) -> tuple[Optional[str], Optional[str]]:
    """`(veredicto, motivo)` de la respuesta del SII; veredicto None si todavía no hay. Un
    documento por sobre: el desglose `ACEPTADOS`/`RECHAZADOS`/`REPAROS` es el suyo (L3)."""
    glosa = (r.get("glosa") or "").strip() or None
    if not r.get("resuelto"):
        return None, glosa
    rechazados, reparos, aceptados = (int(r.get(k) or 0) for k in ("rechazados", "reparos", "aceptados"))
    if tipo in TIPOS_BOLETA and r.get("detalle"):
        glosa = json.dumps(r["detalle"], ensure_ascii=False)[:2000]
    if rechazados:
        if tipo not in TIPOS_BOLETA:
            glosa = ((glosa + ". ") if glosa else "") + "El código del rechazo llega por correo del SII."
        return RECHAZADO, glosa
    if reparos:
        return CON_REPAROS, glosa
    if aceptados:
        return ACEPTADO, glosa
    estado_sobre = (r.get("estado") or "").upper()
    if estado_sobre in _SOBRE_RECHAZADO:
        return RECHAZADO, f"Sobre rechazado por el SII ({estado_sobre}). {glosa or ''}".strip()
    if estado_sobre == "RLV":
        return CON_REPAROS, glosa
    return None, glosa


# ---------------------------------------------------------------------------------------------
# Apoyo
# ---------------------------------------------------------------------------------------------
def _cliente(doc):
    from core.adaptadores.sii import ClienteBoletas, ClienteFacturas
    from core.sii import AmbienteSII
    clase = ClienteBoletas if doc["tipo_dte"] in TIPOS_BOLETA else ClienteFacturas
    return clase(_certificado(doc), AmbienteSII(settings.sii_ambiente))


def _certificado(doc):
    """El certificado del negocio emisor, de la cuenta que emitió (`usuario:<id>`) o, si fue un
    token, de la cuenta dueña de la instancia (`core/autorizacion.py`)."""
    from core.orchestrator import OrquestadorDTE
    from core.servicios.emision import Solicitante, _certificado as certificado_del_negocio
    origen = doc["origen"] or ""
    cuenta = int(origen.split(":", 1)[1]) if origen.startswith("usuario:") else _cuenta_duena()
    return certificado_del_negocio(Solicitante(origen=origen, cuenta_id=cuenta, es_admin=False),
                                   doc["rut_emisor"], OrquestadorDTE())


def _cuenta_duena() -> Optional[int]:
    with datos.transaccion() as con:
        fila = con.execute("SELECT id FROM usuarios WHERE activo=1 ORDER BY id LIMIT 1").fetchone()
    return fila["id"] if fila else None


def _sobre(doc) -> bytes:
    from core.servicios.emision import _rutas
    ruta = _rutas(doc["id"], doc["intento"], doc["tipo_dte"], doc["folio"])["xml_envio"]
    if not ruta.exists():
        raise RecursoNoEncontrado(f"No está el sobre firmado del documento {doc['id']} ({ruta.name}).")
    return ruta.read_bytes()


def _documento(dte_id: int):
    with datos.transaccion() as con:
        fila = con.execute(f"SELECT {_COLUMNAS} FROM dtes WHERE id=?", (dte_id,)).fetchone()
    if fila is None:
        raise RecursoNoEncontrado(f"Documento {dte_id} no existe")
    return fila


def _tomar(dte_id: int) -> bool:
    ahora = _ahora()
    with datos.transaccion() as con:
        return con.execute("UPDATE dtes SET tomado_hasta=? WHERE id=? AND (tomado_hasta IS NULL OR tomado_hasta<?)",
                           (_iso(reloj.ahora() + _TOMA), dte_id, ahora)).rowcount == 1


def _soltar(dte_id: int) -> None:
    with datos.transaccion() as con:
        con.execute("UPDATE dtes SET tomado_hasta=NULL WHERE id=?", (dte_id,))


def _vista(doc) -> dict:
    return {"dte_id": doc["id"], "tipo_dte": doc["tipo_dte"], "folio": doc["folio"],
            "rut_emisor": doc["rut_emisor"], "estado": doc["estado"],
            "track_id": str(doc["track_id"]) if doc["track_id"] is not None else None,
            "motivo_sii": doc["motivo_sii"], "seguimiento": bool(doc["seguimiento"]),
            "intentos_envio": doc["intentos_envio"], "proxima_accion": doc["proxima_accion"],
            "enviado_en": doc["enviado_en"], "consultado_en": doc["consultado_en"],
            "veredicto_en": doc["veredicto_en"], "ambiente": doc["ambiente"],
            "envio_sii": settings.envio_sii}


def _motivo_error(e: BaseException) -> str:
    texto = getattr(e, "mensaje", None) or str(e) or type(e).__name__
    return f"{type(e).__name__}: {texto}"[:500]


def _ahora() -> str:
    return _iso(reloj.ahora())


def _iso(momento: datetime) -> str:
    return momento.isoformat(timespec="microseconds")
