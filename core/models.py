"""
core/models.py

Modelos de datos para operaciones CRUD con la base de datos.
Funciones de acceso a datos para DTEs, CAFs, logs y usuarios.
"""

from __future__ import annotations

import json
import time
from contextvars import ContextVar
from calendar import monthrange
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy.exc import OperationalError

from core import datos, reloj
from core.ambiente import SIMULACION, resolver
from core.database import ejecutar, obtener_todos, obtenerUno, get_db


# =============================================
# DTEs - Documentos Tributarios Electrónicos
# =============================================

# Estados de `dtes` que no tienen un documento timbrado: la fila solo reserva el folio
# (servicio de emisión, docs/deploy/PLAN.md "Política de folios"). No cuentan como emitidos
# y su folio puede volver a usarse.
ESTADOS_SIN_TIMBRE = ("reservado", "error")

# Los clientes del SII marcan los documentos de un sobre después de enviarlo
# (`marcar_enviados_por_xml`). Los adaptadores del fork (`core/adaptadores/sii.py`) lo apagan
# durante su envío: ahí persiste quien llama, dueño de su transacción.
persistir_envios: ContextVar[bool] = ContextVar("persistir_envios", default=True)

def crear_dte(
    tipo_dte: int,
    folio: int,
    rut_emisor: str,
    rut_receptor: str,
    razon_social_receptor: str,
    fecha_emision: str,
    monto_neto: int,
    monto_exento: int,
    iva: int,
    monto_total: int,
    xml_firmado: Optional[str] = None,
    ambiente: str = "certificacion",
) -> int:
    """
    Registra un nuevo DTE en la base de datos.

    Returns:
        ID del DTE creado.
    """
    ahora = datetime.now().isoformat()
    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO dtes
            (tipo_dte, folio, rut_emisor, rut_receptor, razon_social_receptor,
             fecha_emision, monto_neto, monto_exento, iva, monto_total,
             estado, xml_firmado, ambiente, creado_en, actualizado_en)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'generado', ?, ?, ?, ?)""",
            (tipo_dte, folio, rut_emisor, rut_receptor, razon_social_receptor,
             fecha_emision, monto_neto, monto_exento, iva, monto_total,
             xml_firmado, ambiente, ahora, ahora),
        )
        return cursor.lastrowid


def marcar_enviados_por_xml(xml_envio, track_id, ambiente: Optional[str] = None,
                            estado: str = "enviado") -> int:
    """Marca como enviados (con su TrackID) los DTE de un sobre recién enviado al SII.

    Los clientes de envío (`core/sii.py`, `core/sii_boleta.py`) reciben solo el XML del
    sobre, no los `dte_id` — así que aquí se extraen los `(TipoDTE, Folio)` de cada
    documento y el `RutEmisor` de la carátula, y se actualizan las filas que calcen.
    Sin esto, el DTE queda `generado` y sin TrackID en la BD aunque el SII lo haya
    aceptado (pasó con una boleta real de producción: hubo que ir al SII a mirar).

    No pisa estados posteriores (`aceptado`/`rechazado`) ni un track ya grabado; los
    sobres sin documentos (p. ej. un RVD/ConsumoFolios) simplemente no calzan → 0 filas.

    Returns:
        Número de filas actualizadas (0 si el sobre no corresponde a DTEs en esta BD).
    """
    if not persistir_envios.get():
        return 0
    import re as _re
    texto = xml_envio.decode("ISO-8859-1", "replace") if isinstance(xml_envio, bytes) else str(xml_envio)
    m = _re.search(r"<RutEmisor>([^<]+)</RutEmisor>", texto)
    if not m:
        return 0
    rut_emisor = m.group(1).strip()
    pares = _re.findall(r"<TipoDTE>(\d+)</TipoDTE>\s*<Folio>(\d+)</Folio>", texto)
    if not pares:
        return 0
    ahora = datetime.now().isoformat()
    filas = 0
    with get_db() as conn:
        for tipo, folio in set(pares):
            sql = ("UPDATE dtes SET "
                   "estado = CASE WHEN estado='generado' THEN ? ELSE estado END, "
                   "track_id = COALESCE(track_id, ?), actualizado_en=? "
                   "WHERE rut_emisor=? AND tipo_dte=? AND folio=?")
            args = [estado, str(track_id), ahora, rut_emisor, int(tipo), int(folio)]
            if ambiente:
                sql += " AND ambiente=?"
                args.append(ambiente)
            filas += conn.execute(sql, args).rowcount
    return filas


def actualizar_estado_dte(dte_id: int, estado: str, track_id: Optional[int] = None) -> bool:
    """Actualiza el estado de un DTE."""
    ahora = datetime.now().isoformat()
    if track_id:
        ejecutar(
            "UPDATE dtes SET estado=?, track_id=?, actualizado_en=? WHERE id=?",
            (estado, track_id, ahora, dte_id),
        )
    else:
        ejecutar(
            "UPDATE dtes SET estado=?, actualizado_en=? WHERE id=?",
            (estado, ahora, dte_id),
        )
    return True


def obtener_dte(dte_id: int) -> Optional[dict]:
    """Obtiene un DTE por su ID."""
    row = obtenerUno("SELECT * FROM dtes WHERE id=?", (dte_id,))
    return dict(row) if row else None


def obtener_dte_por_folio(rut_emisor: str, tipo_dte: int, folio: int,
                          ambiente: Optional[str] = None) -> Optional[dict]:
    """Obtiene un DTE por RUT emisor, tipo y folio, en `ambiente` (por defecto, el actual): el
    mismo folio existe en certificación y en producción."""
    row = obtenerUno(
        "SELECT * FROM dtes WHERE rut_emisor=? AND tipo_dte=? AND folio=? AND ambiente=?",
        (rut_emisor, tipo_dte, folio, resolver(ambiente)),
    )
    return dict(row) if row else None


def listar_dtes(
    rut_emisor: Optional[str] = None,
    tipo_dte: Optional[int] = None,
    estado: Optional[str] = None,
    fecha_desde: Optional[str] = None,
    fecha_hasta: Optional[str] = None,
    limite: int = 100,
    offset: int = 0,
) -> list[dict]:
    """Lista DTEs con filtros opcionales."""
    condiciones = []
    params = []

    if rut_emisor:
        condiciones.append("rut_emisor=?")
        params.append(rut_emisor)
    if tipo_dte:
        condiciones.append("tipo_dte=?")
        params.append(tipo_dte)
    if estado:
        condiciones.append("estado=?")
        params.append(estado)
    if fecha_desde:
        condiciones.append("fecha_emision>=?")
        params.append(fecha_desde)
    if fecha_hasta:
        condiciones.append("fecha_emision<=?")
        params.append(fecha_hasta)

    where = " AND ".join(condiciones) if condiciones else "1=1"
    params.extend([limite, offset])

    rows = obtener_todos(
        f"SELECT * FROM dtes WHERE {where} ORDER BY creado_en DESC LIMIT ? OFFSET ?",
        tuple(params),
    )
    return [dict(r) for r in rows]


def contar_dtes(
    rut_emisor: Optional[str] = None,
    tipo_dte: Optional[int] = None,
    estado: Optional[str] = None,
) -> int:
    """Cuenta DTEs con filtros opcionales."""
    condiciones = []
    params = []

    if rut_emisor:
        condiciones.append("rut_emisor=?")
        params.append(rut_emisor)
    if tipo_dte:
        condiciones.append("tipo_dte=?")
        params.append(tipo_dte)
    if estado:
        condiciones.append("estado=?")
        params.append(estado)

    where = " AND ".join(condiciones) if condiciones else "1=1"
    row = obtenerUno(f"SELECT COUNT(*) as total FROM dtes WHERE {where}", tuple(params))
    return row["total"] if row else 0


def guardar_pdf_dte(dte_id: int, pdf_path: str) -> bool:
    """Guarda la ruta del PDF generado para un DTE."""
    ahora = datetime.now().isoformat()
    ejecutar(
        "UPDATE dtes SET pdf_path=?, actualizado_en=? WHERE id=?",
        (pdf_path, ahora, dte_id),
    )
    return True


# =============================================
# CAFs - Códigos de Autorización de Folios
# =============================================

def registrar_caf(
    tipo_dte: int,
    rut_emisor: str,
    folio_desde: int,
    folio_hasta: int,
    fecha_autorizacion: str,
    caf_xml: str,
    ambiente: Optional[str] = None,
) -> int:
    """
    Registra un nuevo CAF en la base de datos, en `ambiente` (por defecto, el actual): los CAF
    de certificación y de producción son distintos y no se mezclan.

    Returns:
        ID del CAF registrado.
    """
    ahora = datetime.now().isoformat()
    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO cafs
            (tipo_dte, rut_emisor, folio_desde, folio_hasta, folio_siguiente,
             fecha_autorizacion, caf_xml, creado_en, ambiente)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (tipo_dte, rut_emisor, folio_desde, folio_hasta, folio_desde,
             fecha_autorizacion, caf_xml, ahora, resolver(ambiente)),
        )
        return cursor.lastrowid


# ---- Qué CAF se usa (fase 7 de docs/deploy/PLAN.md) ------------------------------------------
# Una empresa puede tener varios CAF del mismo tipo. Los folios salen del CAF **en uso**
# (`cafs.en_uso`); cuando se agota o vence, el motor pasa solo al siguiente: el vigente que vence
# primero. El administrador puede fijar otro (`usar_caf`). Nunca se emite con un CAF vencido: el
# SII rechaza el documento (`CAF-3-517`) y el folio se pierde.
MESES_VIGENCIA_CAF = 6   # Res. Ex. SII 58/2017. Se aplica a todos los tipos (decisión de la fase 7).


def vence_caf(fecha_autorizacion) -> Optional[date]:
    """El primer día en que el CAF ya no se usa: su fecha de autorización más 6 meses."""
    try:
        fa = date.fromisoformat(str(fecha_autorizacion)[:10])
    except ValueError:
        return None
    mes = fa.month - 1 + MESES_VIGENCIA_CAF
    anio, mes = fa.year + mes // 12, mes % 12 + 1
    return date(anio, mes, min(fa.day, monthrange(anio, mes)[1]))


def caf_vencido(caf, hoy: Optional[date] = None) -> bool:
    """¿Pasaron los 6 meses? Los CAF de la demo (`simulacion`) no vencen: nunca van al SII."""
    if caf["ambiente"] == SIMULACION:
        return False
    vence = vence_caf(caf["fecha_autorizacion"])
    return vence is not None and (hoy or reloj.ahora().date()) >= vence


def caf_utilizable(caf, hoy: Optional[date] = None) -> bool:
    return bool(caf["activo"]) and caf["folio_siguiente"] <= caf["folio_hasta"] and not caf_vencido(caf, hoy)


def _elegir_caf(filas) -> Optional[dict]:
    """De los CAF activos de un emisor, tipo y ambiente: el que está en uso si todavía sirve; si
    no, el vigente con folios que vence primero."""
    utiles = [dict(f) for f in filas if caf_utilizable(f)]
    if not utiles:
        return None
    en_uso = next((c for c in utiles if c["en_uso"]), None)
    return en_uso or min(utiles, key=lambda c: (str(c["fecha_autorizacion"])[:10], c["folio_desde"]))


_SQL_CAFS = ("SELECT * FROM cafs WHERE rut_emisor=? AND tipo_dte=? AND ambiente=? AND activo=1 "
             "ORDER BY folio_desde")


def obtener_caf_activo(rut_emisor: str, tipo_dte: int, ambiente: Optional[str] = None) -> Optional[dict]:
    """El CAF del que sale el próximo folio de un emisor y tipo, en `ambiente` (por defecto, el
    actual). None si no hay ninguno vigente con folios."""
    return _elegir_caf(obtener_todos(_SQL_CAFS, (rut_emisor, tipo_dte, resolver(ambiente))))


def motivo_sin_folios(rut_emisor: str, tipo_dte: int, ambiente: str, conn=None) -> str:
    """Por qué no hay folios: `sin_caf`, `vencido` (hay CAF con folios, pero pasaron los 6 meses)
    o `agotado`. Con `conn`, lee por esa conexión (la de la transacción en curso)."""
    parametros = (rut_emisor, tipo_dte, ambiente)
    filas = (conn.execute(_SQL_CAFS, parametros).fetchall() if conn is not None
             else obtener_todos(_SQL_CAFS, parametros))
    if not filas:
        return "sin_caf"
    return "vencido" if any(f["folio_siguiente"] <= f["folio_hasta"] for f in filas) else "agotado"


def usar_caf(caf_id: int) -> dict:
    """El administrador fija el CAF del que salen los folios de su tipo y ambiente. Tiene que
    estar activo, vigente y con folios."""
    from core.errors import FolioError, RecursoNoEncontrado
    with datos.transaccion(inmediata=True) as conn:
        caf = conn.execute("SELECT * FROM cafs WHERE id=?", (caf_id,)).fetchone()
        if caf is None:
            raise RecursoNoEncontrado(f"CAF {caf_id} no existe")
        if not caf_utilizable(caf):
            motivo = ("está desactivado" if not caf["activo"] else
                      "está vencido (más de 6 meses desde su autorización)" if caf_vencido(caf) else
                      "no tiene folios disponibles")
            raise FolioError(f"El CAF {caf_id} no se puede usar: {motivo}.",
                             detalle={"caf_id": caf_id, "vence": str(vence_caf(caf["fecha_autorizacion"]))})
        conn.execute("UPDATE cafs SET en_uso = CASE WHEN id=? THEN 1 ELSE 0 END "
                     "WHERE rut_emisor=? AND tipo_dte=? AND ambiente=?",
                     (caf_id, caf["rut_emisor"], caf["tipo_dte"], caf["ambiente"]))
        return {**dict(caf), "en_uso": 1}


def listar_cafs(rut_emisor: Optional[str] = None, tipo_dte: Optional[int] = None,
                ambiente: Optional[str] = None) -> list[dict]:
    """Lista todos los CAFs registrados (de todos los ambientes, salvo que se pida uno)."""
    condiciones = []
    params = []

    if ambiente:
        condiciones.append("ambiente=?")
        params.append(ambiente)

    if rut_emisor:
        condiciones.append("rut_emisor=?")
        params.append(rut_emisor)
    if tipo_dte:
        condiciones.append("tipo_dte=?")
        params.append(tipo_dte)

    where = " AND ".join(condiciones) if condiciones else "1=1"
    rows = obtener_todos(f"SELECT * FROM cafs WHERE {where} ORDER BY creado_en DESC", tuple(params))
    return [dict(r) for r in rows]


def actualizar_folio_siguiente(caf_id: int, folio_siguiente: int) -> bool:
    """Actualiza el siguiente folio disponible en un CAF."""
    ejecutar("UPDATE cafs SET folio_siguiente=? WHERE id=?", (folio_siguiente, caf_id))
    return True


def desactivar_caf(caf_id: int) -> bool:
    """Desactiva un CAF."""
    ejecutar("UPDATE cafs SET activo=0 WHERE id=?", (caf_id,))
    return True


def obtener_siguiente_folio(rut_emisor: str, tipo_dte: int, ambiente: Optional[str] = None) -> Optional[int]:
    """Obtiene el siguiente folio disponible para un emisor y tipo de DTE."""
    caf = obtener_caf_activo(rut_emisor, tipo_dte, ambiente)
    if not caf:
        return None
    if caf["folio_siguiente"] > caf["folio_hasta"]:
        return None
    return caf["folio_siguiente"]


def consumir_folio(rut_emisor: str, tipo_dte: int, folio: int, ambiente: Optional[str] = None) -> bool:
    """Marca un folio como consumido (avanza folio_siguiente)."""
    caf = obtener_caf_activo(rut_emisor, tipo_dte, ambiente)
    if not caf:
        return False
    if folio < caf["folio_desde"] or folio > caf["folio_hasta"]:
        return False
    nuevo_siguiente = folio + 1
    actualizar_folio_siguiente(caf["id"], nuevo_siguiente)
    return True


def consumir_siguiente_folio(rut_emisor: str, tipo_dte: int, ambiente: Optional[str] = None) -> Optional[int]:
    """
    Obtiene y consume atómicamente el siguiente folio disponible para un emisor y tipo de DTE.

    Usa BEGIN IMMEDIATE en una sola transacción para evitar el race condition TOCTOU
    que ocurre cuando obtener_siguiente_folio() y consumir_folio() se ejecutan en
    transacciones separadas.

    Returns:
        El folio asignado, o None si no hay folios disponibles.
    """
    max_retries = 3
    retry_delay = 0.05  # 50ms
    # Antes de abrir la transacción: leer el ambiente usa otra conexión, y pedirla con el
    # candado tomado agota el pool cuando hay muchas emisiones a la vez.
    ambiente = resolver(ambiente)

    for attempt in range(max_retries):
        try:
            with datos.transaccion(inmediata=True) as conn:
                caf = bloquear_caf_activo(conn, rut_emisor, tipo_dte, ambiente)
                if not caf:
                    return None
                return avanzar_folio(conn, caf)
        except OperationalError as e:
            if "database is locked" in str(e) and attempt < max_retries - 1:
                time.sleep(retry_delay)
                continue
            raise


def bloquear_caf_activo(conn, rut_emisor: str, tipo_dte: int, ambiente: str):
    """El CAF del que sale el próximo folio (ver `_elegir_caf`), en `ambiente` y **con candado**,
    dentro de una `datos.transaccion(inmediata=True)`. Si el CAF en uso se agotó o venció, deja
    marcado el que sigue. `ambiente` llega resuelto (`core.ambiente.resolver`): aquí no se abre
    otra conexión.

    En PostgreSQL el candado es `SELECT … FOR UPDATE` sobre la fila; en SQLite ya lo dio el
    `BEGIN IMMEDIATE` de la transacción. Es el único lugar donde se toma (L7): lo usan
    `consumir_siguiente_folio` y la reserva del servicio de emisión."""
    candado = " FOR UPDATE" if datos.es_postgres() else ""
    caf = _elegir_caf(conn.execute(_SQL_CAFS + candado, (rut_emisor, tipo_dte, ambiente)).fetchall())
    if caf is not None and not caf["en_uso"]:
        conn.execute("UPDATE cafs SET en_uso = CASE WHEN id=? THEN 1 ELSE 0 END "
                     "WHERE rut_emisor=? AND tipo_dte=? AND ambiente=?",
                     (caf["id"], rut_emisor, tipo_dte, ambiente))
    return caf


def avanzar_folio(conn, caf) -> Optional[int]:
    """Consume `folio_siguiente` del CAF bloqueado por `bloquear_caf_activo`. None si se agotó."""
    folio = caf["folio_siguiente"]
    if folio > caf["folio_hasta"]:
        return None
    conn.execute("UPDATE cafs SET folio_siguiente=? WHERE id=?", (folio + 1, caf["id"]))
    return folio


# =============================================
# Audit Log - Registro de auditoría
# =============================================

def registrar_log(
    accion: str,
    tipo_dte: Optional[int] = None,
    folio: Optional[int] = None,
    rut_emisor: Optional[str] = None,
    detalle: Optional[str] = None,
    ip_origen: Optional[str] = None,
    actor: Optional[str] = None,
    metodo: Optional[str] = None,
    ruta: Optional[str] = None,
    estado_http: Optional[int] = None,
) -> int:
    """Registra una acción en el log de auditoría. `actor` es `usuario:<id>` o `apikey:<id>`
    (fase 4 de docs/deploy/PLAN.md); nunca se guarda el cuerpo de una petición."""
    ahora = datetime.now().isoformat()
    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO audit_log
            (accion, tipo_dte, folio, rut_emisor, detalle, ip_origen, creado_en,
             actor, metodo, ruta, estado_http)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (accion, tipo_dte, folio, rut_emisor, detalle, ip_origen, ahora,
             actor, metodo, ruta, estado_http),
        )
        return cursor.lastrowid


def listar_logs(
    accion: Optional[str] = None,
    rut_emisor: Optional[str] = None,
    fecha_desde: Optional[str] = None,
    limite: int = 100,
    actor: Optional[str] = None,
) -> list[dict]:
    """Lista logs de auditoría con filtros."""
    condiciones = []
    params = []

    if actor:
        condiciones.append("actor=?")
        params.append(actor)

    if accion:
        condiciones.append("accion=?")
        params.append(accion)
    if rut_emisor:
        condiciones.append("rut_emisor=?")
        params.append(rut_emisor)
    if fecha_desde:
        condiciones.append("creado_en>=?")
        params.append(fecha_desde)

    where = " AND ".join(condiciones) if condiciones else "1=1"
    params.append(limite)

    rows = obtener_todos(
        f"SELECT * FROM audit_log WHERE {where} ORDER BY creado_en DESC LIMIT ?",
        tuple(params),
    )
    return [dict(r) for r in rows]


# =============================================
# Usuarios - Autenticación
# =============================================

def crear_usuario(
    username: str,
    hashed_password: str,
    nombre_completo: Optional[str] = None,
    email: Optional[str] = None,
) -> int:
    """Crea un nuevo usuario."""
    ahora = datetime.now().isoformat()
    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO usuarios
            (username, hashed_password, nombre_completo, email, creado_en)
            VALUES (?, ?, ?, ?, ?)""",
            (username, hashed_password, nombre_completo, email, ahora),
        )
        return cursor.lastrowid


def obtener_usuario_por_username(username: str) -> Optional[dict]:
    """Obtiene un usuario por su nombre de usuario."""
    row = obtenerUno("SELECT * FROM usuarios WHERE username=? AND activo=1", (username,))
    return dict(row) if row else None


def actualizar_ultimo_acceso(user_id: int) -> bool:
    """Actualiza la fecha del último acceso del usuario."""
    ahora = datetime.now().isoformat()
    ejecutar("UPDATE usuarios SET ultimo_acceso=? WHERE id=?", (ahora, user_id))
    return True


def listar_usuarios() -> list[dict]:
    """Lista todos los usuarios activos."""
    rows = obtener_todos("SELECT id, username, nombre_completo, email, activo, creado_en, ultimo_acceso FROM usuarios WHERE activo=1")
    return [dict(r) for r in rows]


def cambiar_clave_usuario(user_id: int, hashed_password: str) -> str:
    """Guarda la contraseña nueva y la fecha del cambio: los JWT emitidos antes dejan de valer
    (`core.auth`). Devuelve esa fecha."""
    ahora = datetime.now(timezone.utc).isoformat()
    ejecutar("UPDATE usuarios SET hashed_password=?, clave_cambiada_en=? WHERE id=?",
             (hashed_password, ahora, user_id))
    return ahora


def desactivar_usuario(user_id: int) -> bool:
    """Desactiva un usuario."""
    ejecutar("UPDATE usuarios SET activo=0 WHERE id=?", (user_id,))
    return True
