#!/usr/bin/env bash
set -euo pipefail
umask 077

case "${1:-panel}" in
    panel)
        # Crea config.json la primera vez, aplica migraciones y prepara la cuenta admin (idempotente).
        python -m mgv_central preparar
        # El puerto solo se publica en 127.0.0.1 del host: los encabezados X-Forwarded-* vienen del
        # proxy del host (nginx o Tailscale Serve), así el panel ve HTTPS y la IP real para los límites.
        exec python -m uvicorn mgv_central.panel:app --host 0.0.0.0 --port 8766 \
            --limit-concurrency 48 --no-access-log --proxy-headers --forwarded-allow-ips '*'
        ;;
    tareas)
        exec python /app/docker/tareas.py
        ;;
    *)
        # Comandos sueltos, por ejemplo: docker compose exec panel python -m mgv_central clave-admin
        exec "$@"
        ;;
esac
