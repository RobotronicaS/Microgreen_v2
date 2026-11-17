"""Instala el agente de una Zero a través de su cuenta SSH."""

import errno
import json
from pathlib import Path
import shlex
from threading import Lock
from time import monotonic, sleep

import paramiko

from .configuracion import RAIZ

CLAVES_SSH = Lock()
PROMPT_SUDO = "__MICROGREEN_SUDO__"


def ejecutar(cliente, comando, clave, latido, limite=900):
    canal = cliente.get_transport().open_session(timeout=15)
    salida = ""
    solicitudes_sudo = 0
    proximo_latido = 0
    fin = monotonic() + limite
    try:
        canal.get_pty()
        canal.exec_command("stty -echo && export SUDO_PROMPT=" + shlex.quote(PROMPT_SUDO) + " && " + comando)
        while True:
            if monotonic() >= fin:
                raise TimeoutError("La instalación superó el tiempo de espera. Podés volver a intentar.")
            if monotonic() >= proximo_latido:
                latido()
                proximo_latido = monotonic() + 10
            if canal.recv_ready():
                salida = (salida + canal.recv(16384).decode("utf-8", errors="replace"))[-32768:]
                if PROMPT_SUDO in salida:
                    solicitudes_sudo += 1
                    if solicitudes_sudo > 8:
                        raise ValueError("Revisá la contraseña y los permisos sudo del usuario de la Raspberry.")
                    canal.sendall((clave + "\n").encode("utf-8"))
                    salida = salida.replace(PROMPT_SUDO, "")
            elif canal.exit_status_ready():
                if canal.recv_exit_status() != 0:
                    raise ValueError("La Zero interrumpió la instalación. " + salida[-1800:].strip())
                return salida
            else:
                sleep(0.1)
    finally:
        canal.close()


def conectar_ssh(host, puerto, usuario, clave):
    cliente = paramiko.SSHClient()
    conocidas = RAIZ / "datos" / "ssh_known_hosts"
    try:
        with CLAVES_SSH:
            conocidas.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            conocidas.touch(mode=0o600, exist_ok=True)
            conocidas.chmod(0o600)
            cliente.load_system_host_keys()
            cliente.load_host_keys(str(conocidas))
            cliente.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            cliente.connect(host, port=puerto, username=usuario, password=clave,
                            look_for_keys=False, allow_agent=False, timeout=12,
                            banner_timeout=12, auth_timeout=15)
        cliente.get_transport().set_keepalive(20)
        return cliente
    except Exception:
        cliente.close()
        raise


def instalar(equipo, clave, configuracion, latido, activar):
    paquete = RAIZ / "zero.tar.gz"
    if not paquete.is_file():
        raise ValueError("Actualizá Central con instalar_central.sh para preparar el instalador de las Zero.")
    with conectar_ssh(equipo["host"], equipo["puerto"], equipo["usuario_ssh"], clave) as cliente:
        sudo = "sudo -S -p " + shlex.quote(PROMPT_SUDO) + " -v"
        ejecutar(cliente, "python3 --version && " + sudo, clave, latido, limite=60)
        with cliente.open_sftp() as archivos:
            archivos.get_channel().settimeout(30)
            casa = archivos.normalize(".")
            identidad_central = Path("/etc/machine-id")
            if identidad_central.is_file():
                with archivos.open("/etc/machine-id", "rb") as identidad_zero:
                    if identidad_zero.read(256).strip() == identidad_central.read_bytes().strip():
                        raise ValueError("Esa dirección corresponde a Central. Ingresá la dirección de la Raspberry Pi Zero.")
            destino = casa + "/MicroGreenVision_Zero/central.json"
            try:
                with archivos.open(destino, "rb") as entrada:
                    anterior = json.loads(entrada.read(65537))
                if (anterior.get("vivero"), anterior.get("nodo")) != (equipo["vivero_id"], equipo["nodo_id"]):
                    raise ValueError("Esta Raspberry está vinculada a otro equipo. Abrí su ficha en Equipos Zero.")
            except OSError as error:
                if error.errno != errno.ENOENT:
                    raise
            temporal = casa + "/.microgreen-vincular-" + equipo["id"]
            archivos.mkdir(temporal, mode=0o700)
            completada = False
            try:
                latido("Copiando el instalador a la Zero.")
                archivos.put(str(paquete), temporal + "/zero.tar.gz")
                with archivos.open(temporal + "/central.json", "wb") as salida:
                    salida.write(json.dumps(configuracion).encode("utf-8"))
                archivos.chmod(temporal + "/central.json", 0o600)
                latido("Instalando y comprobando la conexión. Puede tardar varios minutos.")
                comando = (sudo + " && cd " + shlex.quote(temporal)
                           + " && tar -xzf zero.tar.gz && bash zero/vincular_remoto.sh")
                ejecutar(cliente, comando, clave, latido)
                activar()
                completada = True
            finally:
                # La copia previa permite recuperar la credencial si falla la activación en Central.
                restaurar = "" if completada else "if [ -f zero/vincular_remoto.sh ]; then bash zero/vincular_remoto.sh restaurar; fi; "
                limpieza = ("cd " + shlex.quote(temporal) + " && " + restaurar
                            + "cd / && rm -rf -- " + shlex.quote(temporal))
                try:
                    ejecutar(cliente, limpieza, clave, lambda: None, limite=30)
                except (OSError, ValueError, paramiko.SSHException):
                    pass
