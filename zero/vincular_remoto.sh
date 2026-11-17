#!/usr/bin/env bash
set -euo pipefail
umask 077

temporal="$(pwd -P)"
casa="$(cd -- "$HOME" && pwd -P)"
destino="$HOME/MicroGreenVision_Zero"
case "$temporal" in
    "$casa"/.microgreen-vincular-*) ;;
    *) printf '%s\n' 'La carpeta temporal de instalación es inválida.' >&2; exit 1 ;;
esac

restaurar() {
    if [[ -f "$destino/central.json" ]] && cmp -s "$destino/central.json" "$temporal/central.json"; then
        if [[ -f "$temporal/central-anterior.json" ]]; then
            install -m 600 "$temporal/central-anterior.json" "$destino/.central-restaurada.json"
            mv -f -- "$destino/.central-restaurada.json" "$destino/central.json"
        elif [[ -f "$temporal/sin-credencial-anterior" ]]; then
            rm -f -- "$destino/central.json"
        fi
    fi
}

if [[ "${1:-}" == 'restaurar' ]]; then
    restaurar
    exit 0
fi

exec 9>"$HOME/.microgreen-zero-instalacion.lock"
flock -n 9 || { printf '%s\n' 'Ya hay una instalación en curso en esta Raspberry.'; exit 1; }
install -d -m 700 "$destino"
if [[ -e "$destino/central.json" ]]; then
    cp -p -- "$destino/central.json" "$temporal/central-anterior.json"
else
    touch "$temporal/sin-credencial-anterior"
fi
trap 'resultado=$?; if [[ "$resultado" -ne 0 ]]; then restaurar; fi; exit "$resultado"' EXIT
trap 'exit 1' HUP INT TERM
install -m 600 "$temporal/central.json" "$destino/.central-proxima.json"
mv -f -- "$destino/.central-proxima.json" "$destino/central.json"
bash "$temporal/zero/instalar_zero.sh"
python3 "$destino/agente_central.py" altas
