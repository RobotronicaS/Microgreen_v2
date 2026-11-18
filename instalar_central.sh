#!/usr/bin/env bash
set -euo pipefail
umask 077

origen="$(cd -- "$(dirname -- "$0")" && pwd -P)"
destino="$HOME/MicroGreenVision_Central"
unidades="$HOME/.config/systemd/user"
if [[ "$EUID" -eq 0 ]]; then
    printf '%s\n' 'Ejecutá con tu usuario Linux. El instalador pide sudo para preparar PostgreSQL.'
    exit 1
fi
if [[ "$(realpath -m "$destino")" == "$origen" ]]; then
    printf '%s\n' 'Ejecutá el instalador desde la carpeta de código extraída del ZIP.'
    exit 1
fi
exec 9>"$HOME/.microgreen-central-instalacion.lock"
flock -n 9 || { printf '%s\n' 'Ya hay una instalación en curso.'; exit 1; }
apt_paquetes=(postgresql python3-fastapi python3-uvicorn python3-jinja2 python3-pydantic
    python3-psycopg python3-paramiko python3-pil python3-numpy ffmpeg rsync ca-certificates)
if ! sudo apt install -y "${apt_paquetes[@]}"; then
    sudo apt update
    sudo apt install -y "${apt_paquetes[@]}"
fi
sudo systemctl enable --now postgresql.service

install -d -m 700 "$destino" "$unidades"
reserva="$destino/.versiones/$(date -u +%Y%m%dT%H%M%SZ)-$$"
install -d -m 700 "$reserva/unidades"
if [[ -d "$destino/mgv_central" ]]; then
    cp -a -- "$destino/mgv_central" "$reserva/"
fi
for unidad in "$unidades"/microgreen-central-*; do
    if [[ -f "$unidad" ]]; then cp -a -- "$unidad" "$reserva/unidades/"; fi
done

temporizadores=(microgreen-central-importar.timer microgreen-central-analisis.timer
    microgreen-central-media.timer microgreen-central-alertas.timer microgreen-central-mantenimiento.timer
    microgreen-central-respaldo.timer)
servicios=(microgreen-central-importar.service microgreen-central-analisis.service
    microgreen-central-media.service microgreen-central-alertas.service microgreen-central-mantenimiento.service
    microgreen-central-respaldo.service microgreen-central-panel.service)
activas=()
for unidad in microgreen-central-panel.service "${temporizadores[@]}"; do
    if systemctl --user is-active --quiet "$unidad"; then activas+=("$unidad"); fi
done

detener_instalados() {
    for unidad in "$@"; do
        if systemctl --user cat "$unidad" >/dev/null 2>&1; then systemctl --user stop "$unidad"; fi
    done
}

recuperar() {
    resultado=$?
    if [[ "$resultado" -eq 0 ]]; then return; fi
    trap - EXIT
    set +e
    printf 'La actualización se interrumpió. Copia previa en %s\n' "$reserva" >&2
    detener_instalados "${temporizadores[@]}" "${servicios[@]}"
    for unidad in microgreen-central-panel.service "${temporizadores[@]}"; do
        if [[ ! -f "$reserva/unidades/$unidad" ]]; then
            systemctl --user disable "$unidad" 2>/dev/null
        fi
    done
    if [[ -d "$reserva/mgv_central" ]]; then
        cp -a -- "$reserva/mgv_central/." "$destino/mgv_central/"
        for unidad in "$reserva/unidades/"*; do
            if [[ -f "$unidad" ]]; then cp -a -- "$unidad" "$unidades/"; fi
        done
        systemctl --user daemon-reload
        if (( ${#activas[@]} )); then systemctl --user start "${activas[@]}"; fi
    fi
    exit "$resultado"
}
trap recuperar EXIT
detener_instalados "${temporizadores[@]}" "${servicios[@]}"
if [[ -f "$destino/config.json" ]]; then
    install -m 600 "$destino/config.json" "$reserva/config.json"
    MGV_CENTRAL_CONFIG="$destino/config.json" PYTHONPATH="$origen" \
        /usr/bin/python3 -m mgv_central volcar-base --destino "$reserva/base.dump"
fi
install -d -m 700 "$destino/mgv_central"
cp -a -- "$origen/mgv_central/." "$destino/mgv_central/"
tar -czf "$destino/zero.tar.gz" -C "$origen" zero
chmod 600 "$destino/zero.tar.gz"
install -m 600 "$origen/README.md" "$destino/README.md"
install -d -m 700 "$destino/docs"
install -m 600 "$origen"/docs/*.md "$destino/docs/"
cd -- "$destino"
/usr/bin/python3 -m mgv_central preparar
/usr/bin/python3 -m mgv_central importar
install -m 644 "$origen"/systemd/microgreen-central-* "$unidades/"
systemctl --user daemon-reload
sudo loginctl enable-linger "$USER"
systemctl --user enable --now microgreen-central-panel.service "${temporizadores[@]}"

/usr/bin/python3 - <<'PY'
import json
import time
from urllib.error import URLError
from urllib.request import urlopen

for _ in range(20):
    try:
        with urlopen("http://127.0.0.1:8766/salud", timeout=2) as respuesta:
            if json.load(respuesta) == {"estado": "ok", "version": "1.0.0"}:
                print("Central v1.0.0 responde en 127.0.0.1:8766.")
                break
    except (OSError, URLError, ValueError):
        pass
    time.sleep(1)
else:
    raise SystemExit("Central v1.0.0 no respondió en 8766. Revisá microgreen-central-panel.service.")
PY
trap - EXIT
printf 'Copia previa en %s\n' "$reserva"
systemctl --user list-timers 'microgreen-central-*' --no-pager

