"""Cámaras sin contraseña: formulario, perfil y entrega real al agente."""
from html.parser import HTMLParser
import json
from urllib.parse import urlsplit

import pytest

from mgv_central.altas import perfil_camara
from pruebas.test_operacion import cliente, entorno, formulario, postgres, sistema


class Campos(HTMLParser):
    def __init__(self):
        super().__init__()
        self.campos = {}

    def handle_starttag(self, tag, attrs):
        datos = dict(attrs)
        if tag == "input" and datos.get("name"):
            self.campos[datos["name"]] = datos


@pytest.mark.parametrize("tipo", ["tapo", "hikvision", "dahua"])
def test_perfiles_conservan_clave_vacia_y_codifican_usuario(tipo):
    perfil = perfil_camara(dict(tipo=tipo, ip="192.168.1.15", usuario_camara="usuario@casa", clave_camara=""))
    url = urlsplit(perfil["rtsp"])
    assert url.netloc == "usuario%40casa:@192.168.1.15:554"
    assert url.password == ""
    assert url.hostname == "192.168.1.15"


@pytest.mark.parametrize("url", ["rtsp://admin:@192.168.1.15:554/video", "rtsp://192.168.1.15:554/video"])
def test_rtsp_personalizado_admite_clave_vacia_o_sin_autenticacion(url):
    assert perfil_camara(dict(tipo="rtsp", rtsp=url))["rtsp"] == url


@pytest.mark.parametrize("clave", [None, "x" * 257, "abc\x00def"])
def test_claves_invalidas_siguen_rechazadas(clave):
    with pytest.raises(ValueError):
        perfil_camara(dict(tipo="hikvision", ip="192.168.1.15", usuario_camara="admin", clave_camara=clave))


def test_formulario_y_entrega_al_agente_con_clave_vacia(sistema):
    with cliente(sistema) as admin:
        pagina = admin.get("/viveros/piloto/alta-camara")
        assert pagina.status_code == 200
        campos = Campos()
        campos.feed(pagina.text)
        assert "required" not in campos.campos["clave_camara"]
        assert "required" in campos.campos["usuario_camara"]
        respuesta = admin.post("/api/viveros/piloto/altas", data=formulario(
            sistema, accion="agregar", nodo="zero01", camara="SIN-CLAVE", nombre="Prueba",
            tipo="hikvision", ip="192.168.1.15", usuario_camara="admin", clave_camara="",
        ))
        assert respuesta.status_code == 202, respuesta.text
        pedido = admin.post("/api/nodos/piloto/zero01/altas/tomar", headers={
            "Authorization": "Bearer " + sistema.token_nodo,
            "Content-Type": "application/json",
        }, content=json.dumps({}))
        assert pedido.status_code == 200, pedido.text
        assert "rtsp://admin:@192.168.1.15:554/Streaming/Channels/102" in pedido.text
