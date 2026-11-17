"""Ciclo completo de registro y consulta sobre PostgreSQL aislado."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from mgv_central import __version__, acceso, registro
from mgv_central.base import conectar, crear_esquema
from mgv_central.ingesta import importar
from mgv_central.panel import app
from pruebas.carga_150 import ejemplo, ingresar, nombre
from pruebas.test_operacion import cliente, entorno, formulario, postgres, sistema


def registrar(navegador, usuario="visitante", clave="Una-clave-de-prueba", **extras):
    navegador.get("/registrarse")
    return navegador.post("/registrarse", data=dict(csrf=navegador.cookies[registro.COOKIE_REGISTRO],
                          persona="Visitante del ensayo", usuario=usuario, clave=clave, confirmar=clave, **extras),
                          follow_redirects=False)


def test_registro_aprobado_solo_visualiza_invernaculos_autorizados(sistema):
    with conectar(sistema.config) as conexion:
        crear_esquema(conexion)
        conexion.execute("INSERT INTO viveros (id, nombre) VALUES ('otro', 'Otro invernáculo')")
    with TestClient(app, base_url="https://testserver") as visitante:
        assert registrar(visitante, rol="tecnico", administrador="si", activo="si").status_code == 303
        assert acceso.COOKIE not in visitante.cookies
        visitante.get("/ingresar")
        assert visitante.post("/ingresar", data=dict(csrf=visitante.cookies[acceso.COOKIE_INGRESO],
                              usuario="visitante", clave="Una-clave-de-prueba")).status_code == 401
        with conectar(sistema.config) as conexion:
            solicitud = conexion.execute("SELECT * FROM solicitudes_acceso").fetchone()
            assert solicitud["estado"] == "pendiente" and solicitud["clave"] != "Una-clave-de-prueba"
            assert not conexion.execute("SELECT 1 FROM usuarios WHERE nombre = 'visitante'").fetchone()
        with cliente(sistema) as administrador:
            assert "Registros pendientes" in administrador.get("/").text
            assert "Visitante del ensayo" in administrador.get("/gestion").text
            respuesta = administrador.post("/gestion/solicitudes/" + str(solicitud["id"]), data=formulario(
                sistema, accion="aprobar", viveros="piloto", administrador="si", rol="tecnico"), follow_redirects=False)
            assert respuesta.status_code == 303
            foto = nombre(datetime.now(UTC))
            assert administrador.post("/api/nodos/piloto/zero01/fotos/H1-C01/" + foto, content=ejemplo(27),
                                      headers={"Authorization": "Bearer " + sistema.token_nodo, "Content-Type": "image/jpeg"}).status_code == 200
            assert importar(sistema.config)["nuevas"] == 1
        ingresar(visitante, "visitante", "Una-clave-de-prueba")
        inicio = visitante.get("/")
        assert "v" + __version__ in inicio.text and "Otro invernáculo" not in inicio.text
        assert "Registros pendientes" not in inicio.text and "Avisos y respaldos" not in inicio.text
        assert visitante.get("/viveros/piloto").status_code == 200
        historial = visitante.get("/viveros/piloto/camaras/H1-C01")
        assert historial.status_code == 200 and "Tomar foto ahora" not in historial.text
        for ruta in ("/viveros/otro", "/gestion", "/mantenimiento", "/viveros/piloto/ajustes"):
            assert visitante.get(ruta).status_code == 403, ruta
        with conectar(sistema.config) as conexion:
            cuenta = conexion.execute("SELECT * FROM usuarios WHERE nombre = 'visitante'").fetchone()
            assert cuenta["activo"] and not cuenta["administrador"]
            assert conexion.execute("SELECT vivero_id, rol FROM permisos WHERE usuario_id = %s", (cuenta["id"],)).fetchall() == [dict(vivero_id="piloto", rol="consulta")]
            resuelta = conexion.execute("SELECT * FROM solicitudes_acceso").fetchone()
            assert resuelta["estado"] == "aprobada" and resuelta["clave"] is None
            captura = conexion.execute("SELECT id FROM capturas").fetchone()["id"]
            csrf = conexion.execute("SELECT csrf FROM sesiones WHERE usuario_id = %s", (cuenta["id"],)).fetchone()["csrf"]
        assert visitante.get(f"/fotos/{captura}.jpg").status_code == 200
        assert visitante.get(f"/miniaturas/{captura}.jpg").status_code == 200
        assert visitante.post("/api/viveros/piloto/altas", data=dict(csrf=csrf, accion="capturar", nodo="zero01", camara="H1-C01")).status_code == 403
        assert visitante.post("/viveros/piloto/sensores", data=dict(csrf=csrf, csv="")).status_code == 403
        assert visitante.post("/gestion/usuarios", data=dict(csrf=csrf, nombre="intruso", clave="Una-clave-de-prueba", administrador="si")).status_code == 403
        # Repetir una aprobación antigua no repone permisos retirados posteriormente.
        with conectar(sistema.config) as conexion:
            conexion.execute("DELETE FROM permisos WHERE usuario_id = %s", (cuenta["id"],))
        with cliente(sistema) as administrador:
            assert administrador.post("/gestion/solicitudes/" + str(solicitud["id"]), data=formulario(
                sistema, accion="aprobar", viveros="piloto"), follow_redirects=False).status_code == 303
        assert visitante.get("/viveros/piloto").status_code == 403


def test_rechazo_duplicados_y_seleccion_de_invernaculos(sistema):
    with TestClient(app, base_url="https://testserver") as visitante:
        assert registrar(visitante).status_code == 303
        with conectar(sistema.config) as conexion:
            primera = conexion.execute("SELECT * FROM solicitudes_acceso").fetchone()
        assert registrar(visitante, clave="Otra-clave-del-ensayo").status_code == 400
        with conectar(sistema.config) as conexion:
            assert conexion.execute("SELECT count(*) AS n FROM solicitudes_acceso").fetchone()["n"] == 1
            assert conexion.execute("SELECT clave FROM solicitudes_acceso").fetchone()["clave"] == primera["clave"]
        with cliente(sistema) as administrador:
            ruta = "/gestion/solicitudes/" + str(primera["id"])
            for sitios in ([], ["no-existe"]):
                assert administrador.post(ruta, data=formulario(sistema, accion="aprobar", viveros=sitios)).status_code == 400
            assert administrador.post(ruta, data=formulario(sistema, accion="rechazar"), follow_redirects=False).status_code == 303
        with conectar(sistema.config) as conexion:
            rechazada = conexion.execute("SELECT * FROM solicitudes_acceso").fetchone()
            assert rechazada["estado"] == "rechazada" and rechazada["clave"] is None
            assert not conexion.execute("SELECT 1 FROM usuarios WHERE nombre = 'visitante'").fetchone()
        assert registrar(visitante).status_code == 303
        with conectar(sistema.config) as conexion:
            assert conexion.execute("SELECT count(*) AS n FROM solicitudes_acceso WHERE estado = 'pendiente'").fetchone()["n"] == 1


def test_registro_concurrente_no_reemplaza_la_primera_solicitud(sistema):
    def pedir(numero):
        with TestClient(app, base_url="https://testserver") as visitante:
            return registrar(visitante, clave="Clave-del-ensayo-" + str(numero)).status_code
    with ThreadPoolExecutor(max_workers=4) as ejecutor:
        estados = list(ejecutor.map(pedir, range(4)))
    assert estados.count(303) == 1 and estados.count(400) == 3
    with conectar(sistema.config) as conexion:
        assert conexion.execute("SELECT count(*) AS n FROM solicitudes_acceso").fetchone()["n"] == 1


def test_limiter_de_registro_no_bloquea_el_login_existente(sistema):
    with conectar(sistema.config) as conexion:
        conexion.execute("INSERT INTO intentos_login (clave, intentos) VALUES (%s, 60)", (acceso.huella("registro:ip:testclient"),))
        conexion.execute("UPDATE usuarios SET clave = %s WHERE nombre = 'admin'", (acceso.codificar_clave("Clave-admin-del-ensayo"),))
    with TestClient(app, base_url="https://testserver") as visitante:
        assert registrar(visitante).status_code == 429
    with TestClient(app, base_url="https://testserver") as administrador:
        ingresar(administrador, "admin", "Clave-admin-del-ensayo")
