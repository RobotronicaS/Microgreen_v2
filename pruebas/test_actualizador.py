"""Actualizaciones contra un Git local: sin red, sudo ni servicios reales."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest


OFICIAL = "https://github.com/RobotronicaS/Microgreen_v2.git"
RAIZ = Path(__file__).resolve().parents[1]


def git(carpeta, *args):
    return subprocess.run(["git", "-C", str(carpeta), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def fuentes(tmp_path):
    if not shutil.which("git") or os.geteuid() == 0:
        pytest.skip("Requiere Git y un usuario normal, como el instalador.")
    remoto, autor, cliente = (tmp_path / n for n in ("remoto.git", "autor", "cliente"))
    remoto.mkdir(); autor.mkdir()
    git(remoto, "init", "--bare", "--initial-branch=estable")
    git(autor, "init", "--initial-branch=estable")
    git(autor, "config", "user.name", "Pruebas")
    git(autor, "config", "user.email", "pruebas@example.invalid")
    shutil.copyfile(RAIZ / "actualizar_central.sh", autor / "actualizar_central.sh")
    # El marcador queda fuera del clon, igual que la instalación real.
    (autor / "instalar_central.sh").write_text(
        '#!/usr/bin/env bash\nprintf "instalado\\n" >> "$MGV_TEST_MARCA"\nexit "${MGV_TEST_FALLO:-0}"\n')
    git(autor, "add", ".")
    git(autor, "commit", "-m", "Versión inicial")
    git(autor, "remote", "add", "origin", str(remoto))
    git(autor, "push", "origin", "estable")
    git(tmp_path, "clone", str(remoto), str(cliente))
    git(cliente, "remote", "set-url", "origin", OFICIAL)
    marca = tmp_path / "instalaciones.txt"
    env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
           "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": f"url.{remoto}.insteadOf",
           "GIT_CONFIG_VALUE_0": OFICIAL, "MGV_TEST_MARCA": str(marca)}
    def ejecutar(**variables):
        return subprocess.run(["bash", str(cliente / "actualizar_central.sh")],
                              cwd=tmp_path, env={**env, **variables}, text=True, capture_output=True)
    return autor, cliente, marca, ejecutar


def publicar(autor):
    (autor / "nueva-version.txt").write_text("Nueva entrega\n")
    git(autor, "add", ".")
    git(autor, "commit", "-m", "Versión siguiente")
    git(autor, "push", "origin", "estable")
    return git(autor, "rev-parse", "HEAD")


def test_avanza_estable_y_permite_reintentar_instalacion(fuentes):
    autor, cliente, marca, ejecutar = fuentes
    nueva = publicar(autor)
    resultado = ejecutar(MGV_TEST_FALLO="7")
    assert resultado.returncode == 7, resultado.stderr
    assert git(cliente, "rev-parse", "HEAD") == nueva
    assert ejecutar().returncode == 0
    assert marca.read_text().splitlines() == ["instalado", "instalado"]


@pytest.mark.parametrize("cambio", ["modificado", "sin-seguir", "commit-propio"])
def test_conserva_cambios_locales_y_no_ejecuta_instalador(fuentes, cambio):
    autor, cliente, marca, ejecutar = fuentes
    publicar(autor)
    if cambio == "modificado":
        archivo = cliente / "instalar_central.sh"
        archivo.write_text(archivo.read_text() + "# cambio local\n")
    else:
        archivo = cliente / "trabajo-local.txt"
        archivo.write_text("Conservar\n")
        if cambio == "commit-propio":
            git(cliente, "add", ".")
            git(cliente, "-c", "user.name=Pruebas", "-c", "user.email=pruebas@example.invalid",
                "commit", "-m", "Trabajo del operador")
    contenido, previo = archivo.read_bytes(), git(cliente, "rev-parse", "HEAD")
    assert ejecutar().returncode != 0
    assert not marca.exists()
    assert archivo.read_bytes() == contenido
    assert git(cliente, "rev-parse", "HEAD") == previo


def test_adopta_estable_desde_detached_antiguo(fuentes):
    autor, cliente, marca, ejecutar = fuentes
    git(cliente, "checkout", "--detach")
    nueva = publicar(autor)
    resultado = ejecutar()
    assert resultado.returncode == 0, resultado.stderr
    assert git(cliente, "branch", "--show-current") == "estable"
    assert git(cliente, "rev-parse", "HEAD") == nueva
    assert marca.exists()


def test_rechaza_remoto_distinto(fuentes):
    _, cliente, marca, ejecutar = fuentes
    git(cliente, "remote", "set-url", "origin", "https://example.invalid/otro.git")
    resultado = ejecutar()
    assert resultado.returncode != 0
    assert "no corresponde" in resultado.stderr
    assert not marca.exists()
