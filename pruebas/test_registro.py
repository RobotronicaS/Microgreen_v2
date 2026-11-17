"""Pruebas HTTP del formulario, las credenciales y los permisos de registro."""

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest

from mgv_central import __version__, acceso, panel, registro


@pytest.fixture
def pagina_registro(monkeypatch):
    conexion = Mock()
    conexion.transaction.side_effect = lambda: nullcontext()

    def ejecutar(consulta, valores=()):
        if "RETURNING intentos" in consulta:
            resultado = {"intentos": 1}
        elif "SELECT count(*)" in consulta:
            resultado = {"n": 0}
        else:
            resultado = None
        return SimpleNamespace(fetchone=lambda: resultado)

    conexion.execute.side_effect = ejecutar
    config = SimpleNamespace(cookies_seguras=True)
    monkeypatch.setattr(registro, "conectar", lambda config: nullcontext(conexion))
    monkeypatch.setattr(panel, "conectar", lambda config: nullcontext(conexion))
    monkeypatch.setattr(registro, "cargar_configuracion", lambda: config)
    monkeypatch.setattr(panel, "cargar_configuracion", lambda: config)
    monkeypatch.setattr(registro, "usuario_actual", lambda *args: None)
    monkeypatch.setattr(panel, "usuario_actual", lambda *args: None)
    monkeypatch.setattr(acceso, "usuario_actual", lambda *args: None)
    with TestClient(panel.app, base_url="https://testserver") as cliente:
        yield cliente, conexion


def datos_registro(cliente, **cambios):
    cliente.get("/registrarse")
    datos = dict(csrf=cliente.cookies[registro.COOKIE_REGISTRO], persona="Visitante del ensayo",
                 usuario="visitante", clave="Una-clave-de-prueba", confirmar="Una-clave-de-prueba")
    return {**datos, **cambios}


def test_ingreso_y_registro_muestran_la_version_activa(pagina_registro):
    cliente, _ = pagina_registro
    ingreso = cliente.get("/ingresar")
    assert ingreso.status_code == 200 and 'href="/registrarse"' in ingreso.text
    registro = cliente.get("/registrarse")
    assert registro.status_code == 200
    assert "v" + __version__ in ingreso.text and "v" + __version__ in registro.text
    assert cliente.get("/salud").json()["version"] == __version__
    assert "Consulta" in registro.text
    assert not any('name="' + nombre + '"' in registro.text for nombre in ("rol", "administrador", "viveros"))


def test_registro_guarda_hash_y_no_abre_sesion(pagina_registro):
    cliente, conexion = pagina_registro
    datos = datos_registro(cliente, administrador="si", rol="tecnico", activo="si")
    respuesta = cliente.post("/registrarse", data=datos, follow_redirects=False)
    assert respuesta.status_code == 303 and respuesta.headers["location"] == "/registro-recibido"
    inserciones = [c.args for c in conexion.execute.call_args_list if "INSERT INTO solicitudes_acceso" in c.args[0]]
    assert len(inserciones) == 1
    _, (nombre, persona, clave) = inserciones[0]
    assert nombre == "visitante" and persona == datos["persona"]
    assert clave != datos["clave"] and acceso.clave_valida(datos["clave"], clave)
    assert not any("INSERT INTO usuarios" in c.args[0] or "INSERT INTO permisos" in c.args[0] for c in conexion.execute.call_args_list)
    assert acceso.COOKIE not in cliente.cookies
    assert datos["clave"] not in cliente.get("/registro-recibido").text


@pytest.mark.parametrize("cambio", [dict(clave="corta", confirmar="corta"), dict(confirmar="Otra-clave-distinta"),
                                    dict(usuario="admin con espacios"), dict(persona=""), dict(persona="Nombre\nApellido")])
def test_formulario_invalido_no_crea_solicitud(pagina_registro, cambio):
    cliente, conexion = pagina_registro
    datos = datos_registro(cliente, **cambio)
    respuesta = cliente.post("/registrarse", data=datos)
    assert respuesta.status_code == 400
    assert datos["clave"] not in respuesta.text
    assert not any("INSERT INTO solicitudes_acceso" in c.args[0] for c in conexion.execute.call_args_list)


def test_csrf_anonimo_y_cookie_del_formulario(pagina_registro):
    cliente, conexion = pagina_registro
    pagina = cliente.get("/registrarse")
    cookie = pagina.headers["set-cookie"].lower()
    assert all(opcion in cookie for opcion in ("httponly", "secure", "samesite=strict"))
    datos = datos_registro(cliente, csrf="incorrecto")
    respuesta = cliente.post("/registrarse", data=datos)
    assert respuesta.status_code == 403
    assert not any("INSERT INTO" in c.args[0] for c in conexion.execute.call_args_list)


@pytest.mark.parametrize("usuario", [None, dict(id=1, administrador=False, csrf="csrf-prueba")])
def test_solo_administrador_autoriza_registros(pagina_registro, monkeypatch, usuario):
    cliente, conexion = pagina_registro
    monkeypatch.setattr(acceso, "usuario_actual", lambda *args: usuario)
    respuesta = cliente.post("/gestion/solicitudes/1", data=dict(csrf="csrf-prueba", accion="aprobar", viveros="piloto"),
                             follow_redirects=False)
    assert respuesta.status_code == (303 if usuario is None else 403)
    assert not any("INSERT INTO" in c.args[0] for c in conexion.execute.call_args_list)


def test_admin_debe_presentar_csrf(pagina_registro, monkeypatch):
    cliente, conexion = pagina_registro
    monkeypatch.setattr(acceso, "usuario_actual", lambda *args: dict(id=1, administrador=True, csrf="csrf-correcto"))
    assert cliente.post("/gestion/solicitudes/1", data=dict(csrf="falso", accion="aprobar", viveros="piloto")).status_code == 403
    assert not any("INSERT INTO" in c.args[0] for c in conexion.execute.call_args_list)
