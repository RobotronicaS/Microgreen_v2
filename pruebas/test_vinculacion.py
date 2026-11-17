from contextlib import nullcontext
import secrets
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import paramiko
import pytest

from mgv_central import acceso, vinculacion
from mgv_central.receptor import autenticar


class Resultado:
    def __init__(self, valor):
        self.valor = valor

    def fetchone(self):
        return self.valor


class BasePrueba:
    def __init__(self):
        self.consultas = []
        self.token_nodo = acceso.huella("credencial-anterior")
        self.trabajo = None

    def transaction(self):
        return nullcontext()

    def execute(self, consulta, datos=()):
        self.consultas.append((consulta, datos))
        if "SELECT 1 FROM viveros" in consulta:
            return Resultado({"id": "piloto"})
        if "SELECT nombre" in consulta:
            return Resultado({"nombre": "zero01", "token_sha": self.token_nodo})
        if "SELECT count(*)" in consulta:
            return Resultado({"n": 0})
        if consulta.startswith("SELECT id FROM vinculaciones_zero"):
            abierta = self.trabajo if self.trabajo and self.trabajo["estado"] in ("pendiente", "en_curso") else None
            return Resultado(abierta)
        if consulta.startswith("INSERT INTO vinculaciones_zero"):
            self.trabajo = dict(zip(("id", "vivero_id", "nodo_id", "solicitante", "host", "puerto", "usuario_ssh", "token_previo_sha"), datos))
            self.trabajo.update(estado="pendiente", mensaje="Preparando")
            return Resultado(self.trabajo.copy())
        if "SET estado = 'en_curso'" in consulta:
            self.trabajo.update(estado="en_curso", token_sha=datos[0])
        if "SELECT estado FROM vinculaciones_zero" in consulta:
            return Resultado({"estado": self.trabajo["estado"]})
        if "UPDATE nodos SET token_sha" in consulta:
            if self.token_nodo != datos[3]:
                return Resultado(None)
            self.token_nodo = datos[0]
            return Resultado({"id": "zero01"})
        if "SET estado = 'ok'" in consulta:
            self.trabajo["estado"] = "ok"
        if "SET estado = 'error', mensaje = %s" in consulta:
            self.trabajo.update(estado="error", mensaje=datos[0])
        if "RETURNING id" in consulta:
            return Resultado({"id": self.trabajo["id"]})
        return Resultado(None)


@pytest.fixture
def pagina(monkeypatch):
    base = BasePrueba()
    usuario = {"id": 1, "nombre": "admin", "administrador": True, "csrf": "csrf-prueba"}
    monkeypatch.setattr(vinculacion, "conectar", lambda config: nullcontext(base))
    monkeypatch.setattr(vinculacion, "cargar_configuracion", lambda: None)
    monkeypatch.setattr(acceso, "usuario_actual", lambda conexion, peticion: usuario)
    hilos = []
    monkeypatch.setattr(vinculacion, "Thread", lambda **kw: SimpleNamespace(start=lambda: hilos.append(kw)))
    app = FastAPI()
    app.include_router(vinculacion.rutas)
    with TestClient(app, base_url="https://central.example:8766") as cliente:
        yield cliente, base, usuario, hilos


def formulario():
    return dict(csrf="csrf-prueba", host="100.90.80.70", usuario_ssh="ucu2026", clave_ssh="clave-de-la-zero",
                url="https://central.example:8766", puerto="22")


def test_pagina_principal_presenta_el_formulario_sin_emitir_credencial(pagina):
    cliente, base, _, _ = pagina
    anterior = base.token_nodo
    respuesta = cliente.get("/viveros/piloto/nodos/zero01/conectar")
    assert respuesta.status_code == 200
    assert 'name="host"' in respuesta.text
    assert "Conectar e instalar" in respuesta.text
    assert 'value="https://central.example:8766"' in respuesta.text
    assert base.token_nodo == anterior


def test_inicio_conserva_credencial_hasta_confirmar_instalacion(pagina):
    cliente, base, _, hilos = pagina
    anterior = base.token_nodo
    respuesta = cliente.post("/viveros/piloto/nodos/zero01/conectar", data=formulario(), follow_redirects=False)
    assert respuesta.status_code == 303
    assert respuesta.headers["location"].startswith("/vinculaciones-zero/")
    assert len(hilos) == 1
    assert base.token_nodo == anterior
    assert all("clave-de-la-zero" not in repr(datos) for _, datos in base.consultas)


def test_doble_envio_reutiliza_instalacion(pagina):
    cliente, base, _, hilos = pagina
    primera = cliente.post("/viveros/piloto/nodos/zero01/conectar", data=formulario(), follow_redirects=False)
    segunda = cliente.post("/viveros/piloto/nodos/zero01/conectar", data=formulario(), follow_redirects=False)
    assert primera.headers["location"] == segunda.headers["location"]
    assert len(hilos) == 1


@pytest.mark.parametrize("cambio", ("csrf", "administrador"))
def test_inicio_exige_administrador_y_csrf(pagina, cambio):
    cliente, _, usuario, hilos = pagina
    datos = formulario()
    if cambio == "csrf":
        datos["csrf"] = "otra-sesion"
    else:
        usuario["administrador"] = False
    respuesta = cliente.post("/viveros/piloto/nodos/zero01/conectar", data=datos, follow_redirects=False)
    assert respuesta.status_code == 403
    assert hilos == []


@pytest.mark.parametrize("campo,valor", [("host", "127.0.0.1"), ("host", "ip;comando"), ("usuario_ssh", "root"),
                                          ("puerto", "0"), ("url", "http://central.example"), ("clave_ssh", "clave\ncomando")])
def test_validacion_rechaza_datos_inutilizables(campo, valor):
    datos = formulario()
    datos[campo] = valor
    with pytest.raises(ValueError):
        vinculacion.datos_ssh(datos)


def test_error_ssh_permite_reintentar_y_conserva_el_acceso_anterior(pagina, monkeypatch):
    cliente, base, _, _ = pagina
    anterior = base.token_nodo
    cliente.post("/viveros/piloto/nodos/zero01/conectar", data=formulario(), follow_redirects=False)
    def rechazar(*args):
        raise paramiko.AuthenticationException()
    monkeypatch.setattr(vinculacion, "instalar", rechazar)
    vinculacion.vincular(None, base.trabajo.copy(), formulario()["url"], formulario()["clave_ssh"])
    assert base.trabajo["estado"] == "error"
    assert base.token_nodo == anterior
    assert "contraseña" in base.trabajo["mensaje"]


def test_instalacion_confirmada_activa_la_credencial_nueva(pagina, monkeypatch):
    cliente, base, _, _ = pagina
    anterior = base.token_nodo
    cliente.post("/viveros/piloto/nodos/zero01/conectar", data=formulario(), follow_redirects=False)
    def confirmar(equipo, clave, config, latido, activar):
        assert base.token_nodo == anterior
        assert base.trabajo["token_sha"] == acceso.huella(config["token"])
        latido("Comprobando conexión")
        activar()
        assert base.token_nodo == acceso.huella(config["token"])
    monkeypatch.setattr(vinculacion, "instalar", confirmar)
    vinculacion.vincular(None, base.trabajo.copy(), formulario()["url"], formulario()["clave_ssh"])
    assert base.trabajo["estado"] == "ok"
    assert base.token_nodo != anterior


@pytest.mark.parametrize("posicion", ["token_sha", "token_instalacion"])
def test_receptor_acepta_credencial_actual_y_temporal(posicion):
    token = secrets.token_urlsafe(32)
    registro = {"token_sha": None, "token_instalacion": None, "zona": "America/Montevideo"}
    registro[posicion] = acceso.huella(token)
    conexion = SimpleNamespace(execute=lambda *args: Resultado(registro))
    peticion = SimpleNamespace(headers={"authorization": "Bearer " + token})
    assert autenticar(conexion, peticion, "piloto", "zero01") == registro
    peticion.headers["authorization"] = "Bearer " + secrets.token_urlsafe(32)
    with pytest.raises(HTTPException) as rechazo:
        autenticar(conexion, peticion, "piloto", "zero01")
    assert rechazo.value.status_code == 401
