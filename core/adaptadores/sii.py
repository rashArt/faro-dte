"""
core/adaptadores/sii.py — Clientes del SII para los servicios y rutas del fork (fase 3 de
docs/deploy/PLAN.md, hallazgo #13).

Son los mismos `ClienteSII` (facturas, SOAP) y `ClienteBoletaSII` (boletas, REST) verificados
contra el SII vivo, con dos diferencias:

1. **El token se comparte** entre peticiones (y entre procesos, con Redis): semilla → firma →
   token se hace una vez por certificado, ambiente y servicio cada 50 minutos, no en cada
   envío o consulta. El token de facturas y el de boletas son distintos (L9).
2. **No escriben en la base**: el envío no marca los documentos (`marcar_enviados_por_xml`);
   lo hace quien llama, que es dueño de su transacción.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from core.adaptadores.cache import cache
from core.models import persistir_envios
from core.sii import ClienteSII
from core.sii_boleta import ClienteBoletaSII

# Los clientes del SII guardan el token 50 minutos (dura ~1 hora): la caché usa el mismo plazo.
_VIGENCIA = timedelta(minutes=50)


class _TokenCompartido:
    """Mezcla para los clientes del SII: el token sale de la caché antes de pedir uno nuevo."""

    servicio = ""

    def _clave_token(self) -> str:
        return f"sii:token:{self.servicio}:{self.ambiente.value}:{self.certificado.rut_emisor}"

    def obtener_token(self) -> str:
        if not (self._token and self._token_expira and datetime.now() < self._token_expira):
            guardado = cache().obtener(self._clave_token())
            if guardado:
                # La caché ya respeta el vencimiento; aquí basta con que el cliente lo reuse.
                self._token, self._token_expira = guardado, datetime.now() + timedelta(minutes=1)
                return guardado
        token = super().obtener_token()
        restante = int((self._token_expira - datetime.now()).total_seconds()) if self._token_expira \
            else int(_VIGENCIA.total_seconds())
        cache().poner(self._clave_token(), token, restante)
        return token

    def olvidar_token(self) -> None:
        """Si el SII rechaza el token (vencido antes de tiempo), el próximo uso pide uno nuevo."""
        cache().borrar(self._clave_token())
        self._token = self._token_expira = None


class ClienteFacturas(_TokenCompartido, ClienteSII):
    """`ClienteSII` (SOAP, maullin/palena) con token compartido y sin escribir en la base."""

    servicio = "factura"

    def enviar_dte(self, *args, **kwargs):
        marca = persistir_envios.set(False)
        try:
            return super().enviar_dte(*args, **kwargs)
        finally:
            persistir_envios.reset(marca)


class ClienteBoletas(_TokenCompartido, ClienteBoletaSII):
    """`ClienteBoletaSII` (REST, token propio) con token compartido y sin escribir en la base."""

    servicio = "boleta"

    def enviar_boletas(self, *args, **kwargs):
        marca = persistir_envios.set(False)
        try:
            return super().enviar_boletas(*args, **kwargs)
        finally:
            persistir_envios.reset(marca)
