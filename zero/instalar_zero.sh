#!/usr/bin/env bash
set -euo pipefail
umask 077
origen="$(cd -- "$(dirname -- "$0")" && pwd -P)"
destino="$HOME/MicroGreenVision_Zero"
if [[ "$EUID" -eq 0 ]]; then
    printf '%s\n' 'Ejecutá con tu usuario de la Zero. El instalador pide sudo para los paquetes.'
    exit 1
fi
if [[ ! -s "$destino/central.json" ]]; then
    printf '%s\n' 'Guardá la credencial generada desde Central en ~/MicroGreenVision_Zero/central.json.'
    exit 1
fi
paquetes=(python3 ffmpeg iproute2 ca-certificates tzdata)
if ! sudo apt install -y "${paquetes[@]}"; then
    sudo apt update
    sudo apt install -y "${paquetes[@]}"
fi
install -d -m 700 "$destino"
# Actualizar el motor es necesario para activar las capacidades de la versión 1.0.
reserva="$destino/.versiones/$(date -u +%Y%m%dT%H%M%SZ)-$$"
install -d -m 700 "$reserva"
archivos=(capturar_enviar.py descubrir.py panel.py panel.html panel.css panel.js agente_central.py alta_remota.py)
for archivo in "${archivos[@]}"; do
    if [[ -f "$destino/$archivo" ]]; then cp -p -- "$destino/$archivo" "$reserva/"; fi
done
activas=()
for unidad in microgreen-zero-central-captura.timer microgreen-zero-central-envio.timer microgreen-zero-central-altas.timer; do
    if systemctl --user is-active --quiet "$unidad"; then activas+=("$unidad"); fi
    if systemctl --user cat "$unidad" >/dev/null 2>&1; then systemctl --user stop "$unidad"; fi
done
for unidad in microgreen-zero-central-captura.service microgreen-zero-central-envio.service microgreen-zero-central-altas.service; do
    if systemctl --user cat "$unidad" >/dev/null 2>&1; then systemctl --user stop "$unidad"; fi
done
recuperar_motor() {
    resultado=$?
    if [[ "$resultado" -eq 0 ]]; then return; fi
    trap - EXIT
    set +e
    for archivo in "${archivos[@]}"; do
        if [[ -f "$reserva/$archivo" ]]; then install -m 600 "$reserva/$archivo" "$destino/$archivo"; fi
    done
    if (( ${#activas[@]} )); then systemctl --user start "${activas[@]}"; fi
    printf 'La actualización se interrumpió. Copia del motor previo en %s\n' "$reserva" >&2
    exit "$resultado"
}
trap recuperar_motor EXIT
for archivo in capturar_enviar.py descubrir.py panel.py panel.html panel.css panel.js; do
    install -m 600 "$origen/base/$archivo" "$destino/$archivo"
done
if [[ ! -e "$destino/config.json" ]]; then
    install -m 600 "$origen/base/config.ejemplo.json" "$destino/config.json"
fi
chmod 600 "$destino/central.json" "$destino/config.json"
install -m 600 "$origen/agente_central.py" "$origen/alta_remota.py" "$destino/"
python3 "$destino/agente_central.py" probar
install -d -m 700 "$HOME/.config/systemd/user"
install -m 644 "$origen"/systemd/microgreen-zero-central-* "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
sudo loginctl enable-linger "$USER"
legacy_activa=false
if systemctl --user is-active --quiet microgreen-zero-captura.timer; then
    legacy_activa=true
    systemctl --user disable --now microgreen-zero-captura.timer
fi
if ! systemctl --user enable --now microgreen-zero-central-captura.timer microgreen-zero-central-envio.timer microgreen-zero-central-altas.timer; then
    systemctl --user disable --now microgreen-zero-central-captura.timer microgreen-zero-central-envio.timer microgreen-zero-central-altas.timer || true
    if "$legacy_activa"; then
        systemctl --user enable --now microgreen-zero-captura.timer
    fi
    exit 1
fi
systemctl --user list-timers 'microgreen-zero-central-*' --no-pager


trap - EXIT
printf 'Agente Zero v1.0.0 preparado. Copia previa en %s\n' "$reserva"
