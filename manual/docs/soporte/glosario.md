# Glosario

CAF
:   Código de Autorización de Folios. Archivo XML que el SII entrega a la empresa y que autoriza
    un rango de folios para un tipo de documento. Se carga en Faro con `POST /api/v1/caf`.

Certificación
:   Proceso del SII que habilita a una empresa para emitir con un software determinado. También,
    el ambiente de pruebas del SII, sin validez tributaria.

Certificado digital
:   Archivo `.pfx` o `.p12` con la firma electrónica de una persona autorizada por la empresa.
    Faro lo guarda cifrado y firma con él cada documento.

Consumidor final
:   Comprador sin identificar en una boleta. Se usa el RUT `66666666-6`.

`dte_id`
:   Identificador de un documento dentro de Faro. Se usa para el PDF, el XML y el estado.

DTE
:   Documento Tributario Electrónico: factura, boleta o nota emitida y firmada electrónicamente.

Emisor
:   La empresa que emite el documento.

Folio
:   Número correlativo de un documento, autorizado por el SII mediante un CAF. Cada folio se usa
    una sola vez.

`id_externo`
:   Identificador de la venta en tu sistema. Evita duplicar documentos al reintentar.

Instancia
:   Una instalación de Faro dedicada a una empresa, en una red interna.

Negocio
:   La empresa emisora configurada en Faro: RUT, certificado, ambiente y datos del emisor.

Nota de crédito (61)
:   Documento que anula o rebaja otro documento ya aceptado.

Nota de débito (56)
:   Documento que aumenta el monto de otro documento ya aceptado.

Receptor
:   A quien se emite el documento.

Referencia
:   Vínculo de una nota con el documento que corrige: tipo, folio, fecha y motivo.

RUT
:   Rol Único Tributario: identificador de personas y empresas en Chile. Formato `12345678-5`.

SII
:   Servicio de Impuestos Internos, la autoridad tributaria de Chile.

Timbre electrónico (TED)
:   Código de barras PDF417 impreso en el documento. Lo genera Faro con el CAF y permite verificar
    el documento.

Token de integración
:   Credencial de un sistema para llamar a la API. Empieza con `dte_`.

TrackID
:   Número que el SII asigna a un envío. Sirve para consultar el resultado en el sitio del SII.

Veredicto
:   La decisión del SII sobre un documento: aceptado, aceptado con reparos o rechazado.
