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
for archivo in capturar_enviar.py descubrir.py panel.py panel.html panel.css panel.js; do
    if [[ ! -e "$destino/$archivo" ]]; then
        install -m 600 "$origen/base/$archivo" "$destino/$archivo"
    fi
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
