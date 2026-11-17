import socket
from threading import Event, Thread

import paramiko
import pytest

from mgv_central import ssh_zero


@pytest.fixture
def servidor_ssh(monkeypatch, tmp_path):
    conexiones = []
    conectar_original = paramiko.SSHClient.connect
    monkeypatch.setattr(ssh_zero, "RAIZ", tmp_path)

    class Servidor(paramiko.ServerInterface):
        def __init__(self):
            self.recibida = b""
            self.comando = b""

        def check_auth_password(self, usuario, clave):
            return paramiko.AUTH_SUCCESSFUL if (usuario, clave) == ("zero", "clave-de-prueba") else paramiko.AUTH_FAILED

        def get_allowed_auths(self, usuario):
            return "password"

        def check_channel_request(self, tipo, identificador):
            return paramiko.OPEN_SUCCEEDED

        def check_channel_pty_request(self, *args):
            return True

        def check_channel_exec_request(self, canal, comando):
            self.comando = comando

            def responder():
                try:
                    canal.sendall(ssh_zero.PROMPT_SUDO[:7].encode())
                    canal.sendall(ssh_zero.PROMPT_SUDO[7:].encode())
                    while not self.recibida.endswith(b"\n"):
                        bloque = canal.recv(128)
                        if not bloque:
                            return
                        self.recibida += bloque
                    canal.sendall(b"Instalacion comprobada\n")
                    canal.send_exit_status(0)
                    canal.shutdown_write()
                except (EOFError, OSError):
                    pass

            Thread(target=responder, daemon=True).start()
            return True

    def iniciar():
        cliente_socket, servidor_socket = socket.socketpair()
        transporte = paramiko.Transport(servidor_socket)
        transporte.add_server_key(paramiko.RSAKey.generate(2048))
        detener = Event()
        servidor = Servidor()

        def atender():
            try:
                transporte.start_server(server=servidor)
                detener.wait(20)
            except (EOFError, paramiko.SSHException):
                pass

        hilo = Thread(target=atender, daemon=True)
        hilo.start()
        conexiones.append((detener, transporte, cliente_socket, hilo))

        def conectar(cliente, *args, **kwargs):
            return conectar_original(cliente, *args, sock=cliente_socket, **kwargs)

        monkeypatch.setattr(paramiko.SSHClient, "connect", conectar)
        return servidor

    yield iniciar
    for detener, transporte, cliente_socket, hilo in conexiones:
        detener.set()
        transporte.close()
        cliente_socket.close()
        hilo.join(timeout=1)


def test_sudo_envia_la_clave_por_el_canal_y_conserva_la_identidad(servidor_ssh, tmp_path):
    servidor = servidor_ssh()
    with ssh_zero.conectar_ssh("zero.example", 22, "zero", "clave-de-prueba") as cliente:
        resultado = ssh_zero.ejecutar(cliente, "sudo -S -v", "clave-de-prueba", lambda: None, limite=5)
    assert "Instalacion comprobada" in resultado
    assert "clave-de-prueba" not in resultado
    assert b"clave-de-prueba" not in servidor.comando
    assert servidor.comando.startswith(b"stty -echo && ")
    assert servidor.recibida == b"clave-de-prueba\n"
    assert "zero.example" in (tmp_path / "datos/ssh_known_hosts").read_text()


def test_rechaza_un_cambio_de_identidad_ssh(servidor_ssh):
    servidor_ssh()
    ssh_zero.conectar_ssh("zero.example", 22, "zero", "clave-de-prueba").close()
    servidor_ssh()
    with pytest.raises(paramiko.BadHostKeyException):
        ssh_zero.conectar_ssh("zero.example", 22, "zero", "clave-de-prueba")
