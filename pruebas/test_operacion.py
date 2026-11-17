"""Integración v0.7 con PostgreSQL temporal y una Zero simulada.

MGV_TEST_PG_BIN puede indicar la carpeta de initdb, postgres, pg_dump y pg_restore.
Los avisos se simulan. Ninguna prueba contacta cámaras ni envía mensajes externos.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import importlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
from time import sleep
from types import SimpleNamespace

from fastapi.testclient import TestClient
import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
import pytest

from mgv_central.acceso import COOKIE, huella
from mgv_central.base import conectar, crear_esquema, registrar_catalogo
from mgv_central.configuracion import Configuracion
from mgv_central import mantenimiento
from mgv_central.ingesta import importar
from mgv_central.panel import app
from mgv_central.respaldo import entorno_postgres, ultimo_respaldo, verificar, verificar_respaldo
from pruebas.carga_150 import comprobar, ejemplo, nombre


@pytest.fixture(scope="session")
def postgres(tmp_path_factory):
    binario = os.environ.get("MGV_TEST_PG_BIN")
    candidatos = [Path(binario)] if binario else [
        *([Path(shutil.which("initdb")).parent] if shutil.which("initdb") else []),
        *sorted(Path("/usr/lib/postgresql").glob("*/bin"), reverse=True),
    ]
    carpeta = next((p for p in candidatos if (p / "initdb").is_file()), None)
    if carpeta is None or (hasattr(os, "geteuid") and os.geteuid() == 0):
        pytest.skip("La integración requiere PostgreSQL y un usuario sin privilegios de root.")
    raiz = tmp_path_factory.mktemp("postgres-mgv")
    datos = raiz / "datos"
    subprocess.run([str(carpeta / "initdb"), "-D", str(datos), "-U", "mgv_test", "-A", "trust",
                    "--locale=C", "--encoding=UTF8", "--no-sync"], check=True, capture_output=True)
    with socket.socket() as reserva:
        reserva.bind(("127.0.0.1", 0))
        puerto = reserva.getsockname()[1]
    anterior = os.environ.get("PATH", "")
    os.environ["PATH"] = str(carpeta) + os.pathsep + anterior
    with (raiz / "postgres.log").open("w") as registro:
        proceso = subprocess.Popen([str(carpeta / "postgres"), "-D", str(datos), "-h", "127.0.0.1",
                                    "-p", str(puerto), "-c", "unix_socket_directories="],
                                   stdout=registro, stderr=registro)
        parametros = dict(host="127.0.0.1", port=puerto, user="mgv_test", dbname="postgres", autocommit=True)
        try:
            for _ in range(100):
                try:
                    with psycopg.connect(**parametros) as conexion:
                        conexion.execute("CREATE ROLE mgv_app LOGIN NOSUPERUSER NOCREATEDB")
                    break
                except psycopg.OperationalError:
                    if proceso.poll() is not None:
                        pytest.fail((raiz / "postgres.log").read_text())
                    sleep(0.05)
            else:
                pytest.fail("PostgreSQL temporal no quedó disponible.")
            yield parametros
        finally:
            proceso.terminate()
            proceso.wait(timeout=15)
            os.environ["PATH"] = anterior


def crear_base(postgres):
    nombre_base = "mgv_" + secrets.token_hex(6)
    with psycopg.connect(**postgres) as conexion:
        conexion.execute(sql.SQL("CREATE DATABASE {} OWNER mgv_app").format(sql.Identifier(nombre_base)))
    return nombre_base


@pytest.fixture
def entorno(postgres, tmp_path, monkeypatch):
    nombre_base = crear_base(postgres)
    valores = dict(base_datos=dict(host="127.0.0.1", puerto=postgres["port"], nombre=nombre_base,
                                  usuario="mgv_app", clave="clave-de-prueba"),
                   archivo=str(tmp_path / "archivo"), min_libre_gib=0.1, viveros=[
                       dict(id="piloto", nombre="Vivero piloto", nodos=[dict(id="zero01", entrada=str(tmp_path / "entrada"),
                            camaras=[dict(id="H1-C01", nombre="Cámara del ensayo", hilera="1")])]),
                   ])
    config = Configuracion.model_validate(valores)
    config.archivo.mkdir()
    ruta = tmp_path / "config.json"
    ruta.write_text(json.dumps(valores), encoding="utf-8")
    ruta.chmod(0o600)
    monkeypatch.setenv("MGV_CENTRAL_CONFIG", str(ruta))
    return SimpleNamespace(config=config, ruta=ruta, raiz=tmp_path, postgres=postgres)


@pytest.fixture
def sistema(entorno):
    with conectar(entorno.config) as conexion:
        crear_esquema(conexion)
        crear_esquema(conexion)
        registrar_catalogo(conexion, entorno.config)
        sesiones = {}
        for nombre_usuario, rol in (("admin", None), ("tecnico", "tecnico"), ("lector", "consulta")):
            usuario = conexion.execute(
                "INSERT INTO usuarios (nombre, clave, administrador) VALUES (%s, 'sin-clave-utilizable', %s) RETURNING id",
                (nombre_usuario, nombre_usuario == "admin"),
            ).fetchone()["id"]
            if rol:
                conexion.execute("INSERT INTO permisos (usuario_id, vivero_id, rol) VALUES (%s, 'piloto', %s)", (usuario, rol))
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            conexion.execute("INSERT INTO sesiones (token_sha, usuario_id, csrf, vence_en) VALUES (%s, %s, %s, now() + interval '1 hour')",
                             (huella(token), usuario, csrf))
            sesiones[nombre_usuario] = dict(token=token, csrf=csrf)
        token_nodo = secrets.token_urlsafe(32)
        conexion.execute("UPDATE nodos SET token_sha = %s, autorizado_en = now(), alta_en = now(), ultimo_contacto = now(), reporte = %s",
                         (huella(token_nodo), Jsonb(dict(capacidades=["capturar"]))))
    entorno.sesiones = sesiones
    entorno.token_nodo = token_nodo
    return entorno


def cliente(sistema, cuenta="admin"):
    cliente_http = TestClient(app, base_url="https://testserver")
    cliente_http.cookies.set(COOKIE, sistema.sesiones[cuenta]["token"])
    return cliente_http


def formulario(sistema, cuenta="admin", **datos):
    return dict(csrf=sistema.sesiones[cuenta]["csrf"], **datos)


def capturar_desde_panel(sistema, navegador):
    return navegador.post("/api/viveros/piloto/altas", data=formulario(
        sistema, accion="capturar", nodo="zero01", camara="H1-C01"))


def test_captura_manual_conserva_configuracion_y_entrega_foto(sistema, monkeypatch):
    raiz_codigo = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(raiz_codigo / "zero" / "base"))
    monkeypatch.syspath_prepend(str(raiz_codigo / "zero"))
    alta_remota = importlib.import_module("alta_remota")
    agente = importlib.import_module("agente_central")
    zero = sistema.raiz / "zero"
    zero.mkdir()
    local = dict(destino=dict(habilitado=False), camaras=[dict(
        id="H1-C01", nombre="Local", rtsp="rtsp://usuario:clave-local@192.168.1.42:554/stream2", activa=True,
    )])
    (zero / "config.json").write_text(json.dumps(local), encoding="utf-8")
    config_antes = (zero / "config.json").read_bytes()
    instante = datetime.now(UTC)
    llamadas_captura = []

    def tomar_foto(camara, carpeta):
        llamadas_captura.append(camara)
        foto = carpeta / camara["id"] / instante.date().isoformat() / nombre(instante)
        foto.parent.mkdir(parents=True)
        foto.write_bytes(ejemplo(1))
        return foto, "ok"

    monkeypatch.setattr(alta_remota, "capturar", tomar_foto)
    monkeypatch.setattr(agente, "RAIZ", zero)
    monkeypatch.setattr(agente, "COLA", zero / "pendientes")
    monkeypatch.setattr(agente, "POLITICA", zero / "politica.json")
    cabeceras = {"Authorization": "Bearer " + sistema.token_nodo}
    with cliente(sistema) as navegador:
        pagina = navegador.get("/viveros/piloto/camaras/H1-C01")
        assert pagina.status_code == 200 and "Tomar foto ahora" in pagina.text
        primera = capturar_desde_panel(sistema, navegador)
        assert primera.status_code == 202, primera.text
        alta = primera.json()["id"]
        assert capturar_desde_panel(sistema, navegador).json()["id"] == alta
        assert f'data-solicitud="{alta}"' in navegador.get("/viveros/piloto/camaras/H1-C01?captura=" + alta).text
        desconocida = secrets.token_hex(16)
        assert f'data-solicitud="{desconocida}"' not in navegador.get("/viveros/piloto/camaras/H1-C01?captura=" + desconocida).text
        acuse_perdido = True

        def peticion(ruta, contenido=None, tipo="application/json"):
            nonlocal acuse_perdido
            respuesta = navegador.post("/api/nodos/piloto/zero01" + ruta,
                                      headers={**cabeceras, "Content-Type": tipo}, content=contenido)
            assert respuesta.status_code == 200, respuesta.text
            if ruta.endswith("/resultado") and acuse_perdido:
                acuse_perdido = False
                raise OSError("Acuse interrumpido durante el ensayo")
            return respuesta.json()

        with pytest.raises(OSError):
            alta_remota.atender_altas(zero, peticion)
        assert (zero / "alta_camara.json").is_file()
        # El reintento confirma el mismo pedido sin volver a tomar la foto.
        pendiente = alta_remota.atender_altas(zero, peticion)
        assert pendiente.is_file() and len(llamadas_captura) == 1
        assert llamadas_captura[0]["rtsp"] == local["camaras"][0]["rtsp"]
        assert (zero / "config.json").read_bytes() == config_antes
        assert not (zero / "alta_camara.json").exists()
        assert agente.informe([pendiente], "ok")["capacidades"] == ["capturar"]
        monkeypatch.setattr(agente, "llamar", lambda datos, ruta, contenido=None, tipo="application/json": peticion(ruta, contenido, tipo))
        agente.entregar_jpg({}, pendiente)
        assert not pendiente.exists()
        assert importar(sistema.config)["nuevas"] == 1
        resultado = navegador.get("/api/viveros/piloto/altas/" + alta).json()
        assert resultado["estado"] == "ok"
        assert navegador.get(resultado["miniatura"]).status_code == 200
        assert "clave-local" not in json.dumps(resultado)
    with conectar(sistema.config) as conexion:
        assert conexion.execute("SELECT count(*) AS n FROM auditoria WHERE accion = 'tomar foto ahora'").fetchone()["n"] == 1


def test_captura_exige_rol_csrf_capacidad_y_asignacion(sistema):
    api = "/api/viveros/piloto/altas"
    with cliente(sistema, "lector") as lector:
        assert "Tomar foto ahora" not in lector.get("/viveros/piloto/camaras/H1-C01").text
        assert lector.post(api, data=formulario(sistema, "lector", accion="capturar", nodo="zero01", camara="H1-C01")).status_code == 403
        assert lector.get("/mantenimiento").status_code == 403
    with cliente(sistema) as administrador:
        assert administrador.post(api, data=dict(csrf="incorrecto", accion="capturar", nodo="zero01", camara="H1-C01")).status_code == 403
        assert administrador.post(api, data=formulario(sistema, accion="capturar", nodo="zero01", camara="H1-C02")).status_code == 404
        # Un agente anterior sigue reportando y agregando cámaras. No recibe pedidos nuevos de captura.
        reporte = administrador.post("/api/nodos/piloto/zero01/estado", headers={"Authorization": "Bearer " + sistema.token_nodo},
                                     json=dict(libres_bytes=8 * 1024**3, pendientes=0))
        assert reporte.status_code == 200
        assert capturar_desde_panel(sistema, administrador).status_code == 409
        pagina = administrador.get("/viveros/piloto/camaras/H1-C01")
        assert re.search(r'<button[^>]*disabled[^>]*>Tomar foto ahora', pagina.text)
        with conectar(sistema.config) as conexion:
            conexion.execute("UPDATE nodos SET reporte = %s", (Jsonb(dict(capacidades=["capturar"])),))
        assert capturar_desde_panel(sistema, administrador).status_code == 202


def datos_avisos(sistema):
    return formulario(sistema, correo_habilitado="si", correo_servidor="smtp.example.test", correo_puerto="587",
                      correo_modo="starttls", correo_usuario="microgreen", correo_clave="secreto-smtp-prueba",
                      correo_desde="mgv@example.test", correo_para="docente@example.test,alumno@example.test",
                      telegram_habilitado="si", telegram_token="123456:token-de-prueba", telegram_chat="-100123456")


def test_avisos_guardan_credenciales_sin_exponerlas_y_prueban_en_segundo_plano(sistema, monkeypatch):
    enviados = []
    monkeypatch.setattr(mantenimiento, "enviar_correo", lambda opciones, asunto, texto: enviados.append(("correo", opciones)))
    monkeypatch.setattr(mantenimiento, "enviar_telegram", lambda opciones, asunto, texto: enviados.append(("telegram", opciones)))
    with conectar(sistema.config) as conexion:
        conexion.execute("INSERT INTO alertas (clave, nivel, detalle) VALUES ('prueba', 'aviso', 'Alerta del ensayo')")
    with cliente(sistema) as navegador:
        datos = datos_avisos(sistema)
        assert navegador.post("/mantenimiento/avisos", data={**datos, "csrf": "incorrecto"}).status_code == 403
        assert not sistema.ruta.with_name("avisos.json").exists()
        assert navegador.post("/mantenimiento/avisos", data=datos, follow_redirects=False).status_code == 303
        with conectar(sistema.config) as conexion:
            assert conexion.execute("SELECT count(*) AS n FROM avisos").fetchone()["n"] == 2
            conexion.execute("UPDATE avisos SET intentos = 5")
        assert navegador.post("/mantenimiento/avisos", data={**datos, "correo_servidor": "smtp2.example.test"},
                              follow_redirects=False).status_code == 303
        with conectar(sistema.config) as conexion:
            assert conexion.execute("SELECT intentos FROM avisos WHERE canal = 'correo'").fetchone()["intentos"] == 0
            assert conexion.execute("SELECT intentos FROM avisos WHERE canal = 'telegram'").fetchone()["intentos"] == 5
        assert sistema.ruta.with_name("avisos.json").stat().st_mode & 0o777 == 0o600
        assert navegador.post("/mantenimiento/avisos", data={**datos, "correo_clave": "", "telegram_token": ""},
                              follow_redirects=False).status_code == 303
        opciones = mantenimiento.leer_opciones("avisos")
        assert opciones["correo"]["clave"] == datos["correo_clave"]
        assert opciones["telegram"]["token"] == datos["telegram_token"]
        pagina = navegador.get("/mantenimiento")
        assert pagina.status_code == 200
        assert all(valor not in pagina.text for valor in (datos["correo_clave"], datos["telegram_token"]))
        for canal in ("correo", "telegram"):
            nonce = secrets.token_hex(16)
            envio = formulario(sistema, tipo=canal, nonce=nonce)
            primera = navegador.post("/mantenimiento/operaciones", data=envio, follow_redirects=False)
            assert primera.status_code == 303
            assert navegador.post("/mantenimiento/operaciones", data=envio, follow_redirects=False).headers["location"] == primera.headers["location"]
            assert not any(c == canal for c, _ in enviados)
            mantenimiento.procesar_mantenimiento(sistema.config)
            mantenimiento.procesar_mantenimiento(sistema.config)
            assert len([c for c, _ in enviados if c == canal]) == 1
            assert navegador.post("/mantenimiento/operaciones", data=envio, follow_redirects=False).headers["location"] == primera.headers["location"]
    with cliente(sistema, "tecnico") as tecnico:
        assert tecnico.post("/mantenimiento/avisos", data=formulario(sistema, "tecnico")).status_code == 403
        assert tecnico.post("/mantenimiento/operaciones", data=formulario(sistema, "tecnico", tipo="correo", nonce=secrets.token_hex(16))).status_code == 403


def test_cola_resiste_concurrencia_interrupcion_y_errores_sin_filtrar_claves(sistema, monkeypatch):
    def encolar(_):
        with conectar(sistema.config) as conexion:
            return mantenimiento.encolar(conexion, "correo", clave="correo:" + secrets.token_hex(16))

    with ThreadPoolExecutor(max_workers=6) as ejecutor:
        pedidos = list(ejecutor.map(encolar, range(6)))
    assert len(set(pedidos)) == 1
    mantenimiento.guardar_opciones("avisos", {"correo": {
        "servidor": "smtp.example.test", "puerto": 587, "modo": "starttls", "usuario": "ensayo", "clave": "secreto-no-publicar",
        "desde": "mgv@example.test", "para": ["docente@example.test"],
    }})

    def fallar(*args):
        raise OSError("Falla simulada secreto-no-publicar")

    monkeypatch.setattr(mantenimiento, "enviar_correo", fallar)
    mantenimiento.procesar_mantenimiento(sistema.config)
    with conectar(sistema.config) as conexion:
        terminado = conexion.execute("SELECT * FROM mantenimiento WHERE id = %s", (pedidos[0],)).fetchone()
        assert terminado["estado"] == "error" and "secreto-no-publicar" not in terminado["mensaje"]
        assert conexion.execute("SELECT pg_try_advisory_lock(431007103) AS ok").fetchone()["ok"]
        pendiente = mantenimiento.encolar(conexion, "telegram")
        mantenimiento.procesar_mantenimiento(sistema.config)
        assert conexion.execute("SELECT estado FROM mantenimiento WHERE id = %s", (pendiente,)).fetchone()["estado"] == "pendiente"
        conexion.execute("SELECT pg_advisory_unlock(431007103)")
        conexion.execute("UPDATE mantenimiento SET estado = 'en_curso' WHERE id = %s", (pendiente,))
    mantenimiento.procesar_mantenimiento(sistema.config)
    with conectar(sistema.config) as conexion:
        assert conexion.execute("SELECT estado FROM mantenimiento WHERE id = %s", (pendiente,)).fetchone()["estado"] == "error"


def test_respaldo_diario_desactivado_y_deduplicado(sistema):
    mantenimiento.programar_respaldo(sistema.config)
    mantenimiento.guardar_opciones("respaldo", dict(destino=str(sistema.raiz / "respaldos"), habilitado=False))
    mantenimiento.programar_respaldo(sistema.config)
    with conectar(sistema.config) as conexion:
        assert conexion.execute("SELECT count(*) AS n FROM mantenimiento").fetchone()["n"] == 0
    mantenimiento.guardar_opciones("respaldo", dict(destino=str(sistema.raiz / "respaldos"), habilitado=True))
    mantenimiento.programar_respaldo(sistema.config)
    mantenimiento.programar_respaldo(sistema.config)
    with conectar(sistema.config) as conexion:
        trabajos = conexion.execute("SELECT * FROM mantenimiento").fetchall()
        assert len(trabajos) == 1 and trabajos[0]["clave"].startswith("diario:")
        conexion.execute("UPDATE mantenimiento SET estado = 'ok', terminada_en = now()")
    mantenimiento.programar_respaldo(sistema.config)
    with conectar(sistema.config) as conexion:
        assert conexion.execute("SELECT count(*) AS n FROM mantenimiento").fetchone()["n"] == 1


def test_respaldo_real_se_verifica_y_se_restaura_en_otra_base(sistema):
    if not shutil.which("rsync"):
        pytest.skip("La prueba de respaldo requiere rsync.")
    destino = sistema.raiz / "respaldos"
    with cliente(sistema) as navegador:
        for invalido in ("carpeta-relativa", str(sistema.config.archivo / "respaldos")):
            assert navegador.post("/mantenimiento/respaldo", data=formulario(sistema, destino=invalido)).status_code == 400
        assert navegador.post("/mantenimiento/respaldo", data=formulario(sistema, destino=str(destino), habilitado="si"),
                              follow_redirects=False).status_code == 303
        foto = ejemplo(42)
        subida = navegador.post("/api/nodos/piloto/zero01/fotos/H1-C01/" + nombre(datetime.now(UTC)), content=foto,
                                headers={"Authorization": "Bearer " + sistema.token_nodo, "Content-Type": "image/jpeg"})
        assert subida.status_code == 200
        assert importar(sistema.config)["nuevas"] == 1
        assert navegador.post("/mantenimiento/avisos", data=datos_avisos(sistema), follow_redirects=False).status_code == 303
        for tipo in ("respaldo", "verificar"):
            pedido = navegador.post("/mantenimiento/operaciones", data=formulario(sistema, tipo=tipo, nonce=secrets.token_hex(16)),
                                    follow_redirects=False)
            assert pedido.status_code == 303
            mantenimiento.procesar_mantenimiento(sistema.config)
            operacion = pedido.headers["location"].split("operacion=")[1].split("#")[0]
            resultado = navegador.get("/api/mantenimiento/" + operacion).json()
            assert resultado["estado"] == "ok", resultado
        assert resultado["resultado"]["completa"] and resultado["resultado"]["archivos"] >= 2
    respaldo = ultimo_respaldo(destino)
    assert (respaldo / "avisos.json").read_bytes() == sistema.ruta.with_name("avisos.json").read_bytes()
    assert respaldo.stat().st_mode & 0o777 == 0o700
    base_restaurada = crear_base(sistema.postgres)
    with entorno_postgres(sistema.config) as (opciones, ambiente):
        subprocess.run(["pg_restore", *opciones, "--exit-on-error", "--no-owner", "--no-acl", "-d", base_restaurada,
                        str(respaldo / "base.dump")], env=ambiente, check=True, capture_output=True)
    assert verificar(sistema.config, respaldo / "archivo", base_restaurada)
    # El verificador señala la corrupción y distingue las copias anteriores sin manifiesto.
    original = next((respaldo / "archivo" / "originales").rglob("*.jpg"))
    original.write_bytes(b"corrupcion-de-prueba")
    with pytest.raises(ValueError, match="dañado"):
        verificar_respaldo(respaldo)
    control = json.loads((respaldo / "completo.json").read_text())
    control.pop("manifiesto_sha256")
    (respaldo / "completo.json").write_text(json.dumps(control))
    assert verificar_respaldo(respaldo)["completa"] is False


def test_diez_invernaculos_ciento_cincuenta_camaras(entorno):
    comprobar(entorno.config)
