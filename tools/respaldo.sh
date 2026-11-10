#!/usr/bin/env bash
# tools/respaldo.sh — respaldo y restauración de UNA instancia de cliente (docker-compose.prod.yml).
#
# Se corre desde la carpeta del cliente, la que tiene docker-compose.yml y .env:
#
#   cd /srv/clientes/acme
#   /srv/faro-dte/tools/respaldo.sh respaldar                # respaldos/<proyecto>-<fecha>.tar.gz
#   /srv/faro-dte/tools/respaldo.sh restaurar <archivo>      # pide confirmación
#   /srv/faro-dte/tools/respaldo.sh restaurar <archivo> --si # sin preguntar (simulacros)
#
# Un respaldo lleva:
#   base.dump           la base completa (pg_dump, formato custom)
#   storage.tar.gz      el volumen storage/: XML firmados y PDF (el SII exige conservarlos)
#   .env                los secretos, incluida DTE_MASTER_KEY: sin ella los certificados
#                       guardados no se descifran
#   docker-compose.yml  el compose del cliente
#   MANIFIESTO.txt      fecha, versión de la imagen, migración de Alembic y conteos
#
# Quien tenga el archivo puede descifrar los certificados del cliente: guárdalo protegido y
# fuera del servidor (un respaldo que vive solo en el VPS se pierde con el VPS).
#
# Restaurar reemplaza la base y el volumen de esta instancia por los del respaldo. Si la base
# tiene datos, antes deja un respaldo de lo que había (sufijo -antes-de-restaurar).
#
# Variables: RESPALDOS_DIR (respaldos), RETENCION_DIAS (30; 0 = no borra respaldos viejos).
set -euo pipefail
export MSYS_NO_PATHCONV=1   # Git Bash en Windows: no convertir /app/... en rutas de Windows
umask 077                   # respaldos y temporales solo para el dueño

RESPALDOS_DIR="${RESPALDOS_DIR:-respaldos}"
RETENCION_DIAS="${RETENCION_DIAS:-30}"

# Carpetas temporales de esta ejecución; se borran al salir.
TEMPORALES=()
limpiar() { [ ${#TEMPORALES[@]} -eq 0 ] || rm -rf "${TEMPORALES[@]}"; }
trap limpiar EXIT
temporal() { local t; t="$(mktemp -d)"; TEMPORALES+=("$t"); TMP="$t"; }

falla() { echo "ERROR: $*" >&2; exit 1; }
dc() { docker compose "$@"; }
psql_db() { dc exec -T db psql -U faro -d faro -tAc "$1"; }
valor_env() { grep -E "^$1=" "${2:-.env}" | tail -n1 | cut -d= -f2- || true; }
corriendo() { dc ps --status running --services | grep -qx "$1"; }

proyecto() {
    local p
    p="$(valor_env COMPOSE_PROJECT_NAME)"
    echo "${p:-$(basename "$PWD")}"
}

# El volumen storage/ como tar.gz por la salida estándar. Con Faro corriendo se lee desde su
# contenedor; si está detenido, desde uno temporal con los mismos volúmenes.
volcar_storage() {
    if corriendo faro; then
        dc exec -T faro tar czf - -C /app/storage .
    else
        dc run --rm --no-deps -T --entrypoint tar faro czf - -C /app/storage .
    fi
}

respaldar() {
    local sufijo="${1:-}" proy fecha nombre tmp destino TMP
    [ -f docker-compose.yml ] || falla "corre el script en la carpeta del cliente (falta docker-compose.yml)"
    [ -f .env ] || falla "falta .env en $(pwd)"
    corriendo db || falla "la base no está corriendo (docker compose up -d)"
    proy="$(proyecto)"
    fecha="$(date +%Y%m%d-%H%M%S)"
    nombre="${proy}-${fecha}${sufijo:+-$sufijo}"
    temporal
    tmp="$TMP"

    echo "Respaldo de $proy"
    # 1. La base primero: un archivo del volumen sin su fila es inofensivo; una fila sin su
    #    archivo no. Lo que se emita durante el respaldo queda, como mucho, en el primer caso.
    dc exec -T db pg_dump -U faro -d faro --format=custom > "$tmp/base.dump"
    dc exec -T db pg_restore --list < "$tmp/base.dump" > /dev/null \
        || falla "el volcado de la base no se puede leer"
    echo "  base:    $(du -h "$tmp/base.dump" | cut -f1)"

    # 2. XML firmados y PDF.
    volcar_storage > "$tmp/storage.tar.gz"
    tar tzf "$tmp/storage.tar.gz" > /dev/null || falla "el volcado de storage/ no se puede leer"
    echo "  storage: $(du -h "$tmp/storage.tar.gz" | cut -f1)"

    # 3. Configuración y manifiesto.
    cp .env docker-compose.yml "$tmp/"
    {
        echo "proyecto=$proy"
        echo "fecha=$(date -Iseconds)"
        echo "faro_version=$(valor_env FARO_VERSION)"
        echo "alembic=$(psql_db 'SELECT version_num FROM alembic_version' || echo '?')"
        echo "documentos=$(psql_db 'SELECT count(*) FROM dtes' || echo '?')"
        echo "cafs=$(psql_db 'SELECT count(*) FROM cafs' || echo '?')"
        echo "certificados=$(psql_db 'SELECT count(*) FROM certificados' || echo '?')"
    } > "$tmp/MANIFIESTO.txt"

    mkdir -p "$RESPALDOS_DIR"
    destino="$RESPALDOS_DIR/$nombre.tar.gz"
    tar czf "$destino" -C "$tmp" .
    echo "  listo:   $destino ($(du -h "$destino" | cut -f1))"

    if [ "$RETENCION_DIAS" -gt 0 ] && [ -z "$sufijo" ]; then
        find "$RESPALDOS_DIR" -maxdepth 1 -name "${proy}-*.tar.gz" -mtime +"$RETENCION_DIAS" -print -delete \
            | sed 's/^/  borrado (más de '"$RETENCION_DIAS"' días): /'
    fi
}

restaurar() {
    local archivo="${1:-}" confirmar="${2:-}" proy tmp clave_actual clave_respaldo respuesta TMP
    [ -n "$archivo" ] || falla "uso: respaldo.sh restaurar <archivo.tar.gz> [--si]"
    [ -f "$archivo" ] || falla "no existe $archivo"
    temporal
    tmp="$TMP"
    # Por la entrada estándar: GNU tar toma "C:\…" como host:ruta.
    tar xzf - -C "$tmp" < "$archivo"
    for f in base.dump storage.tar.gz .env docker-compose.yml MANIFIESTO.txt; do
        [ -f "$tmp/$f" ] || falla "el respaldo no trae $f"
    done

    # Una instancia nueva toma la configuración del respaldo.
    if [ ! -f docker-compose.yml ]; then
        cp "$tmp/docker-compose.yml" docker-compose.yml
        echo "docker-compose.yml tomado del respaldo"
    fi
    if [ ! -f .env ]; then
        cp "$tmp/.env" .env
        chmod 600 .env
        echo ".env tomado del respaldo"
    fi
    # Con otra DTE_MASTER_KEY los certificados restaurados no se descifran: no se sigue.
    clave_actual="$(valor_env DTE_MASTER_KEY)"
    clave_respaldo="$(valor_env DTE_MASTER_KEY "$tmp/.env")"
    [ "$clave_actual" = "$clave_respaldo" ] \
        || falla "la DTE_MASTER_KEY del .env actual no es la del respaldo. Usa el .env que viene en el respaldo (tar xzf - ./.env < $archivo)."

    proy="$(proyecto)"
    echo "Restaurar en $proy:"
    sed 's/^/  /' "$tmp/MANIFIESTO.txt"
    if [ "$confirmar" != "--si" ]; then
        read -r -p "Esto reemplaza la base y el volumen de $proy. Escribe '$proy' para seguir: " respuesta
        [ "$respuesta" = "$proy" ] || falla "cancelado"
    fi

    dc up -d --wait db
    if [ "$(psql_db "SELECT to_regclass('public.dtes') IS NOT NULL")" = "t" ]; then
        echo "La base tiene datos: primero se respalda lo que hay."
        # En un subshell, con su propia limpieza: no toca la carpeta temporal de esta restauración.
        ( TEMPORALES=(); trap limpiar EXIT; respaldar antes-de-restaurar )
    fi

    dc stop faro > /dev/null 2>&1 || true
    echo "Restaurando la base…"
    dc exec -T db pg_restore -U faro -d faro --clean --if-exists --no-owner \
        --single-transaction --exit-on-error < "$tmp/base.dump"
    echo "Restaurando storage/…"
    dc run --rm --no-deps -T --entrypoint tar faro xzf - -C /app/storage < "$tmp/storage.tar.gz"

    # migrar corre antes de Faro: un respaldo de una versión anterior queda al día.
    echo "Levantando la instancia…"
    dc up -d --wait faro
    echo "Listo. Documentos en la base: $(psql_db 'SELECT count(*) FROM dtes')"
}

case "${1:-}" in
    respaldar) respaldar ;;
    restaurar) shift; restaurar "$@" ;;
    *) sed -n '2,25p' "$0"; exit 2 ;;
esac
