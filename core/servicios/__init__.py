"""
core/servicios — Casos de uso del fork (docs/deploy/ARQUITECTURA.md, punto 2).

Cada servicio es dueño de su transacción: las rutas validan HTTP y llaman aquí; el corazón SII
(`core/sobre.py`, `core/dte.py`, `core/caf.py`…) se llama sin modificarlo.
"""
