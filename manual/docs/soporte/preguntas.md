# Preguntas frecuentes

## Sobre el modelo

??? question "¿Por qué el veredicto no viene en la respuesta de emitir?"
    Porque el SII no valida en línea: recibe el documento, entrega un número de seguimiento y lo
    revisa después. Faro responde al instante con el documento listo y sigue el veredicto por ti.
    Ver [Cómo funciona](../empezar/como-funciona.md).

??? question "¿Puedo entregar el PDF antes del veredicto?"
    El PDF está timbrado desde la emisión. Las boletas se entregan de inmediato. En facturas, si
    tu proceso exige confirmar la aceptación del SII antes de enviarla al cliente, espera el estado
    `aceptado`.

??? question "¿Cuánto tarda el SII?"
    Boletas: segundos a pocos minutos. Facturas: minutos a horas. Si pasan 6 horas sin
    veredicto, el documento queda `sin_veredicto` y Faro sigue preguntando.

??? question "¿Necesito llamar a algo para que Faro envíe al SII?"
    No. Con `enviar: true` (el valor por defecto), Faro envía y sigue el veredicto solo. Ver
    [Enviar tú mismo al SII](../guias/envio-manual.md) si quieres controlarlo.

## Sobre la emisión

??? question "¿Tengo que mandar los datos de mi empresa en cada documento?"
    No. Se configuran una vez en la instancia (ver
    [Configurar la empresa](../guias/configurar-empresa.md)) y Faro los usa en cada emisión.

??? question "¿Tengo que calcular el IVA y los totales?"
    No. Mandas cantidad y precio unitario; Faro calcula neto, IVA y total. En facturas y notas el
    precio es neto; en boletas incluye IVA.

??? question "¿Puedo elegir el folio?"
    No hace falta: Faro asigna el siguiente disponible sin repetir, aunque lleguen muchas ventas a
    la vez.

??? question "Mandé una factura y no recibí respuesta. ¿La repito?"
    Sí, con el mismo JSON y el mismo `id_externo`. Si ya se había emitido, recibes el mismo
    documento con `repetido: true`. Ver [Reintentos e idempotencia](../guias/idempotencia.md).

??? question "Me equivoqué en una factura. ¿Cómo la edito?"
    No se edita: se corrige con una nota de crédito o débito que la referencia. Si el SII la
    rechazó, se emite una nueva. Ver [Corregir o anular](../guias/corregir-anular.md).

??? question "¿Cómo anulo una boleta?"
    Con una nota de crédito (61) que la referencia con `codigo_ref: 1`. Ver
    [Corregir o anular](../guias/corregir-anular.md).

## Sobre el acceso

??? question "¿El token vence?"
    No. Se recomienda renovarlo cada cierto tiempo con `POST /api/v1/apikeys/propia/renovar`. Al
    renovar, la clave anterior deja de funcionar de inmediato.

??? question "Tengo dos sistemas. ¿Comparten token?"
    Mejor no: pide un token por sistema. Así cada uno ve sus documentos, la auditoría distingue
    quién hizo qué y puedes revocar uno sin afectar al otro.

??? question "¿Por qué recibo 404 en un documento que existe?"
    Cada token ve solo los documentos que emitió. Si lo emitió otro sistema, consúltalo con ese
    token.

??? question "¿Puedo llamar a Faro desde el navegador o una app móvil?"
    No. Faro vive en una red interna y el token es un secreto: llámalo siempre desde tu backend.

## Sobre los ambientes

??? question "¿Cómo pruebo sin generar documentos reales?"
    Con una instancia de desarrollo (envío desactivado) o con
    [Previsualizar](../guias/previsualizar.md), que no gasta folios. Ver
    [Ambientes](../referencia/ambientes.md).

??? question "¿Tengo que cambiar mi código para pasar a producción?"
    No. Cambian la URL de la instancia y el token; las llamadas son las mismas.
