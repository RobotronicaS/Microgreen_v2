#!/usr/bin/env bash
set -euo pipefail
umask 077
cd -- "$(dirname -- "$0")/.."
export MGV_IA_TOKEN_FILE="${MGV_IA_TOKEN_FILE:-/workspace/microgreen/servicio_ia/token.txt}"
if [[ ! -s "$MGV_IA_TOKEN_FILE" ]]; then
    printf '%s\n' 'Primero configurá el acceso con python -m runpod_ia.configurar_token.' >&2
    exit 1
fi
exec /workspace/microgreen/venv-ia/bin/python -m uvicorn runpod_ia.api:app --host "${MGV_IA_HOST:-127.0.0.1}" --port 8767 --workers 1 --no-access-log
