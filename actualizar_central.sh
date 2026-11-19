#!/usr/bin/env bash
# Ejecutar desde el clon de fuentes, con el usuario Linux de Central.
set -euo pipefail
umask 077

fallar() { printf '%s\n' "$*" >&2; exit 1; }
origen="$(cd -- "$(dirname -- "$0")" && pwd -P)"
[[ "$EUID" -ne 0 ]] || fallar 'Ejecutá con el usuario Linux de Central, sin sudo.'
command -v git >/dev/null || fallar 'Falta Git. Instalalo con sudo apt install git.'
raiz="$(git -C "$origen" rev-parse --show-toplevel)" || fallar 'Ejecutá desde un clon de GitHub, no desde un ZIP.'
[[ "$(cd -- "$raiz" && pwd -P)" == "$origen" ]] || fallar 'El actualizador debe estar en la raíz del clon.'
[[ "$origen" != "$(realpath -m "$HOME/MicroGreenVision_Central")" ]] || fallar 'Usá una carpeta de fuentes separada de la instalación.'
remoto="$(git -C "$origen" config --get remote.origin.url)"
case "$remoto" in
    https://github.com/RobotronicaS/Microgreen_v2|https://github.com/RobotronicaS/Microgreen_v2.git|git@github.com:RobotronicaS/Microgreen_v2.git|ssh://git@github.com/RobotronicaS/Microgreen_v2.git) ;;
    *) fallar 'El remoto origin no corresponde a RobotronicaS/Microgreen_v2.' ;;
esac
cd -- "$origen"
exec 8>"$(git rev-parse --git-path microgreen-actualizacion.lock)"
flock -n 8 || fallar 'Ya hay una actualización de estas fuentes en curso.'
[[ -z "$(git status --porcelain)" ]] || fallar 'Hay cambios locales. Guardalos en otra rama o carpeta antes de actualizar; no se sobrescribieron.'
git fetch --no-tags origin '+refs/heads/estable:refs/remotes/origin/estable'
git merge-base --is-ancestor HEAD refs/remotes/origin/estable || fallar 'El código local no pertenece al historial estable. Usá un clon nuevo en otra carpeta.'
if git show-ref --verify --quiet refs/heads/estable; then
    git merge-base --is-ancestor refs/heads/estable refs/remotes/origin/estable || fallar 'La rama estable local tiene commits propios. Usá otro clon para instalar.'
    git switch estable
else
    git switch --create estable --track origin/estable
fi
git merge --ff-only refs/remotes/origin/estable
git branch --set-upstream-to=origin/estable estable >/dev/null
printf 'Instalando rama estable, commit %s\n' "$(git rev-parse --short=12 HEAD)"
bash -n "$origen/instalar_central.sh"
exec bash "$origen/instalar_central.sh"
