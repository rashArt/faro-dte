# ============================================
# Faro (motor Python) - Dockerfile
# ============================================
# La misma imagen para desarrollo (docker-compose.dev.yml) y para cada cliente
# (docker-compose.prod.yml). La configuración llega por variables de entorno (`env_file`):
# la imagen no lleva `.env` ni secretos, y corre sin privilegios.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# SQLite por defecto DENTRO del volumen storage/: el usuario faro no escribe en /app. El compose
# de producción la reemplaza por PostgreSQL.
ENV DATABASE_URL=sqlite:////app/storage/dte_database.db

WORKDIR /app

# Dependencias del sistema para compilar lxml si no hay wheel
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc \
        libxml2-dev \
        libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

# Requirements primero (caché de capas)
COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir -r requirements.txt -c constraints.txt

# Usuario sin privilegios. UID/GID fijos: los volúmenes y los respaldos conservan el mismo dueño
# entre versiones de la imagen.
RUN groupadd --system --gid 10001 faro && \
    useradd --system --uid 10001 --gid faro --create-home faro

COPY . .

# Solo storage/ (base SQLite, XML firmados, PDF) y logs/ son escribibles. Un volumen nuevo
# hereda este dueño; uno creado con una imagen anterior (que corría como root) se corrige una
# vez: docker compose run --rm --user 0 faro chown -R faro:faro /app/storage /app/logs
RUN mkdir -p /app/storage /app/logs && chown -R faro:faro /app/storage /app/logs

USER faro

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health').raise_for_status()"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
