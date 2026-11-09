"""
core/pdf_borrador.py — Marca "BORRADOR" sobre un PDF (fase 5 de docs/deploy/PLAN.md).

La previsualización firma y timbra con un folio de prueba: el PDF se ve igual a uno real. La
marca evita que alguien lo entregue por error. Se estampa encima de cada página con pypdf, sin
tocar el generador de PDF (`core/pdf_gen.py`), que comparte la emisión real.
"""
from __future__ import annotations

import io

from pypdf import PdfReader, PdfWriter
from reportlab.lib.colors import Color
from reportlab.pdfgen import canvas

TEXTO = "BORRADOR"


def _sello(ancho: float, alto: float) -> PdfReader:
    salida = io.BytesIO()
    c = canvas.Canvas(salida, pagesize=(ancho, alto))
    c.setFillColor(Color(0.85, 0.1, 0.1, alpha=0.30))
    tamano = min(ancho, alto) / 5
    c.setFont("Helvetica-Bold", tamano)
    c.translate(ancho / 2, alto / 2)
    c.rotate(35 if alto >= ancho else 20)
    c.drawCentredString(0, -tamano / 3, TEXTO)
    c.setFont("Helvetica-Bold", tamano / 5)
    c.drawCentredString(0, -tamano, "Sin validez tributaria: previsualización")
    c.save()
    salida.seek(0)
    return PdfReader(salida)


def marcar_borrador(pdf: bytes) -> bytes:
    """El mismo PDF con "BORRADOR" en diagonal sobre cada página."""
    escritor = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf)))
    for pagina in escritor.pages:
        ancho, alto = float(pagina.mediabox.width), float(pagina.mediabox.height)
        pagina.merge_page(_sello(ancho, alto).pages[0])
    salida = io.BytesIO()
    escritor.write(salida)
    return salida.getvalue()
