"""
tests/scripts/_raiz.py

Los scripts de esta carpeta se escribieron para correr desde la raíz del repositorio
(importan `core` y usan rutas como `storage/` o `tests/dorado/`). Al importarlo, deja la
raíz en `sys.path` y como directorio de trabajo, así siguen funcionando igual:

    python tests/scripts/test_dorado.py
"""
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))
os.chdir(RAIZ)
