"""Prueba aislada de migración, acceso e importación con 10 invernáculos y 150 cámaras."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from html import unescape
from io import BytesIO
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from mgv_central.acceso import codificar_clave, huella
from mgv_central.base import conectar, crear_esquema, registrar_catalogo
from mgv_central.configuracion import cargar_configuracion, ruta_configuracion
from mgv_central.dominio import PUESTOS
from mgv_central.ingesta import importar, preparar_foto
from mgv_central.panel import app
from mgv_central.procesos import procesar
from mgv_central.respaldo import dump_base, verificar

SITIOS = ["piloto", *(f"sitio{n:02d}" for n in range(2, 11))]
CAMARA = "H1-C01"
NODO = "zero01"


def ejemplo(numero):
    imagen = Image.new("RGB", (320, 240), (100, 89, 76))
    dibujo = ImageDraw.Draw(imagen)
    for n in range(30):
        x, y = (19 * n + numero) % 300, (13 * n + numero) % 220
        dibujo.ellipse((x, y, x + 16, y + 16), fill=(22, 175, 57))
    memoria = BytesIO()
    imagen.save(memoria, "JPEG", quality=90)
    return memoria.getvalue()


def nombre(momento):
    return momento.strftime("%Y%m%dT%H%M%S_%fZ.jpg")


def poblar(config):
    legacy = config.viveros[0].nodos[0].entrada
    momento = datetime.now(UTC) - timedelta(days=1)
    foto = ejemplo(0)
    raiz = Path(__file__).resolve().parents[1] / "mgv_central"
    version_previa = (raiz / "esquema.sql").read_text(encoding="utf-8").split("ALTER TABLE viveros", 1)[0]
    with conectar(config) as conexion:
        conexion.execute(version_previa)
        conexion.execute("INSERT INTO viveros (id, nombre) VALUES ('piloto', 'Vivero piloto')")
        conexion.execute("INSERT INTO camaras (vivero_id, id, nodo_id, nombre, hilera) VALUES ('piloto', %s, %s, %s, '1')",
                         (CAMARA, NODO, CAMARA))
        clave = f"piloto/{CAMARA}/{momento.date().isoformat()}/{nombre(momento)}"
        ancho, alto, miniatura = preparar_foto(foto, 40)
        for clase, contenido in (("originales", foto), ("miniaturas", miniatura)):
            ruta = config.archivo / clase / clave
            ruta.parent.mkdir(parents=True, exist_ok=True)
            ruta.write_bytes(contenido)
        anterior = conexion.execute(
            """INSERT INTO capturas (vivero_id, camara_id, capturada_en, clave_archivo, sha256, bytes, ancho, alto)
            VALUES ('piloto', %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (CAMARA, momento, clave, sha256(foto).hexdigest(), len(foto), ancho, alto),
        ).fetchone()["id"]
        copia = legacy / CAMARA / momento.date().isoformat() / nombre(momento)
        copia.parent.mkdir(parents=True)
        copia.write_bytes(foto)
        crear_esquema(conexion)
        crear_esquema(conexion)
        registrar_catalogo(conexion, config)
        tokens = {sitio: secrets.token_urlsafe(32) for sitio in SITIOS}
        for sitio, token in tokens.items():
            conexion.execute("INSERT INTO viveros (id, nombre) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                             (sitio, f"Invernáculo de prueba {sitio}"))
            conexion.execute(
                """INSERT INTO nodos (vivero_id, id, nombre, token_sha, autorizado_en) VALUES (%s, %s, %s, %s, now())
                ON CONFLICT (vivero_id, id) DO UPDATE SET token_sha = EXCLUDED.token_sha""",
                (sitio, NODO, NODO, huella(token)),
            )
            with conexion.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO camaras (vivero_id, id, nodo_id, nombre, hilera)
                    VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                    [(sitio, camara, NODO, camara, hilera) for camara, hilera in PUESTOS],
                )
        conexion.execute("""INSERT INTO camaras_origen (vivero_id, nodo_id, camara_id)
                          SELECT vivero_id, nodo_id, id FROM camaras ON CONFLICT DO NOTHING""")
        acceso = secrets.token_urlsafe(16)
        consulta = secrets.token_urlsafe(16)
        conexion.execute("INSERT INTO usuarios (nombre, clave, administrador) VALUES ('admin', %s, true)",
                         (codificar_clave(acceso),))
        lector = conexion.execute("INSERT INTO usuarios (nombre, clave) VALUES ('lector', %s) RETURNING id",
                                  (codificar_clave(consulta),)).fetchone()["id"]
        conexion.execute("INSERT INTO permisos (usuario_id, vivero_id, rol) VALUES (%s, 'piloto', 'consulta')", (lector,))
    return tokens, acceso, consulta, anterior


def ingresar(cliente, usuario, clave):
    respuesta = cliente.get("/ingresar")
    csrf = re.search(r'name="csrf" value="([^"]+)"', respuesta.text)[1]
    ingreso = cliente.post("/ingresar", data={"usuario": usuario, "clave": clave, "csrf": csrf}, follow_redirects=False)
    assert ingreso.status_code == 303, ingreso.status_code
    assert cliente.get("/").status_code == 200


def subir(cliente, sitio, camara, momento, foto, token):
    return cliente.post(f"/api/nodos/{sitio}/{NODO}/fotos/{camara}/{nombre(momento)}",
                        headers={"Authorization": f"Bearer {token}", "Content-Type": "image/jpeg"}, content=foto)


def enviar_sitio(cliente, sitio, token, momento, numero):
    for p, (camara, _) in enumerate(PUESTOS):
        respuesta = subir(cliente, sitio, camara, momento, ejemplo(numero * 15 + p + 1), token)
        assert respuesta.status_code == 200 and respuesta.json()["recibida"], respuesta.text[:200]


def comprobar(config):
    tokens, acceso, consulta, anterior = poblar(config)
    momento = datetime.now(UTC)
    inicio = monotonic()
    with TestClient(app, base_url="https://testserver") as cliente:
        assert cliente.get("/", follow_redirects=False).status_code == 303
        assert cliente.get("/supervisor?parcial=true", follow_redirects=False).headers["location"] == "/ingresar"
        assert subir(cliente, "sitio02", CAMARA, momento, ejemplo(1), tokens["piloto"]).status_code == 401
        with ThreadPoolExecutor(max_workers=10) as ejecutor:
            cargas = [ejecutor.submit(enviar_sitio, cliente, sitio, token, momento, n)
                      for n, (sitio, token) in enumerate(tokens.items())]
            for carga in cargas:
                carga.result()
        resultado = importar(config)
        assert resultado["nuevas"] == 150 and resultado["errores"] == 0, resultado
        assert importar(config)["nuevas"] == 0
        assert subir(cliente, "piloto", CAMARA, momento, ejemplo(1), tokens["piloto"]).json()["duplicada"]
        assert subir(cliente, "piloto", CAMARA, momento, ejemplo(999), tokens["piloto"]).status_code == 409
        atraso = momento - timedelta(days=4)
        assert subir(cliente, "piloto", CAMARA, atraso, ejemplo(82), tokens["piloto"]).status_code == 200
        assert subir(cliente, "piloto", CAMARA, atraso - timedelta(minutes=30), b"jpg-danado", tokens["piloto"]).status_code == 200
        resultado = importar(config)
        assert resultado["nuevas"] == 1 and resultado["rechazadas"] == 1, resultado
        for sitio in ("piloto", "sitio02"):
            reporte = cliente.post(f"/api/nodos/{sitio}/{NODO}/estado",
                                   headers={"Authorization": f"Bearer {tokens[sitio]}"},
                                   json={"libres_bytes": 8 * 1024**3, "pendientes": 0})
            assert reporte.status_code == 200, reporte.text
        with conectar(config) as conexion:
            conexion.execute("UPDATE nodos SET ultimo_contacto = now() - interval '11 minutes' WHERE vivero_id = 'sitio02'")
            conexion.execute("INSERT INTO trabajos (vivero_id, tipo, estado, datos) VALUES ('sitio02', 'zip', 'error', '{}')")
        ingresar(cliente, "admin", acceso)
        for ruta in ("/", "/viveros/piloto", "/viveros/piloto/ajustes", "/supervisor", "/diagnostico", "/alertas", "/trabajos",
                     "/gestion", "/equipos-zero", "/viveros/piloto/camaras/H1-C01?lote=", "/viveros/piloto/lotes/nuevo",
                     "/viveros/piloto/sensores"):
            respuesta = cliente.get(ruta)
            assert respuesta.status_code == 200, (ruta, respuesta.status_code, respuesta.text[:250])
        supervisor = cliente.get("/supervisor?parcial=true")
        assert supervisor.status_code == 200 and "data-supervisor-datos" in supervisor.text
        assert "Servidor central" in supervisor.text and "En contacto" in supervisor.text
        assert "Sin contacto" in supervisor.text and "1 trabajo con error." in supervisor.text
        assert all(f'data-supervisor-seccion="sitio-{sitio}"' in supervisor.text for sitio in SITIOS)
        assert cliente.get(f"/fotos/{anterior}.jpg").status_code == 200
        csrf = re.search(r'name="csrf" value="([^"]+)"', cliente.get("/").text)[1]
        assert cliente.post("/viveros/piloto/lotes", data={"csrf": "erroneo"}).status_code == 403
        lote = cliente.post("/viveros/piloto/lotes", follow_redirects=False, data={
            "csrf": csrf, "camara": CAMARA, "nombre": "Lote de prueba", "especie": "Rúcula",
            "siembra": (momento - timedelta(days=2)).date().isoformat(),
            "x": "0", "y": "0", "ancho": "100", "alto": "100",
        })
        assert lote.status_code == 303 and cliente.get(lote.headers["location"]).status_code == 200
        assert cliente.get(lote.headers["location"] + "/mediciones.csv").status_code == 200
        assert cliente.post("/viveros/piloto/sensores", data={
            "csrf": csrf, "csv": "fecha,temp_c,humedad_pct,lux,co2_ppm\n" + momento.isoformat() + ",21,70,200,440",
        }, follow_redirects=False).status_code == 303
        with conectar(config) as conexion:
            ajena = conexion.execute("SELECT id FROM capturas WHERE vivero_id = 'sitio02' LIMIT 1").fetchone()["id"]
        cliente.cookies.clear()
        ingresar(cliente, "lector", consulta)
        supervisor = cliente.get("/supervisor?parcial=true")
        assert supervisor.status_code == 200 and 'data-supervisor-seccion="sitio-piloto"' in supervisor.text
        assert all(texto not in supervisor.text for texto in ("sitio02", "Servidor central", "Sin contacto", "trabajo con error"))
        equipos = cliente.get("/equipos-zero")
        assert equipos.status_code == 200 and "Vivero piloto" in equipos.text
        assert "Agregar Zero" not in equipos.text and "sitio02" not in equipos.text
        assert cliente.get("/equipos-zero?vivero=sitio02").status_code == 404
        csrf = re.search(r'name="csrf" value="([^"]+)"', equipos.text)[1]
        assert cliente.post("/equipos-zero", data=dict(csrf=csrf, vivero="piloto", nombre="Copia")).status_code == 403
        assert cliente.get("/viveros/sitio02").status_code == 403
        assert cliente.get(f"/fotos/{ajena}.jpg").status_code == 403
        assert cliente.get(f"/miniaturas/{ajena}.jpg").status_code == 403
        assert cliente.get("/viveros/piloto/ajustes").status_code == 403
        assert cliente.get(f"/miniaturas/{anterior}.jpg").status_code == 200
    for _ in range(12):
        if not procesar(config):
            break
    with conectar(config) as conexion:
        assert conexion.execute("SELECT count(*) AS cantidad FROM calidad").fetchone()["cantidad"] == 152
        assert conexion.execute("SELECT count(*) AS cantidad FROM mediciones").fetchone()["cantidad"] >= 1
    comprobar_altas(config, acceso, tokens["piloto"])
    assert verificar(config)
    dump_base(config, config.archivo.parent / "prueba.dump")
    print(f"150 JPG de 10 invernáculos, recuperación atrasada, migración, permisos y rechazo verificados en {monotonic() - inicio:.1f} s.")


def comprobar_altas(config, acceso, token_ajeno):
    sitio, camara = "alta-prueba", "H1-C01"
    token = secrets.token_urlsafe(32)
    with conectar(config) as conexion:
        conexion.execute("INSERT INTO viveros (id, nombre) VALUES (%s, 'Prueba de alta')", (sitio,))
        conexion.execute(
            "INSERT INTO nodos (vivero_id, id, nombre, token_sha) VALUES (%s, %s, %s, %s)",
            (sitio, NODO, NODO, huella(token)),
        )
    api = f"/api/viveros/{sitio}/altas"
    nodo = f"/api/nodos/{sitio}/{NODO}/altas"
    encabezados = {"Authorization": f"Bearer {token}"}
    with TestClient(app, base_url="https://testserver") as cliente:
        ingresar(cliente, "admin", acceso)
        pagina = cliente.get(f"/viveros/{sitio}/alta-camara")
        assert pagina.status_code == 200 and 'data-disponible="no"' in pagina.text
        csrf = re.search(r'name="csrf" value="([^"]+)"', pagina.text)[1]
        formulario = dict(csrf=csrf, nodo=NODO)
        assert cliente.post(api, data={**formulario, "accion": "buscar"}).status_code == 409
        assert cliente.post(nodo + "/tomar", headers={"Authorization": f"Bearer {token_ajeno}"}).status_code == 401
        assert cliente.post(nodo + "/tomar", headers=encabezados).json() == {"solicitud": None}
        buscar = cliente.post(api, data={**formulario, "accion": "buscar"}).json()["id"]
        assert cliente.post(nodo + "/tomar", headers=encabezados).json()["solicitud"]["id"] == buscar
        resultado = cliente.post(f"{nodo}/{buscar}/resultado", headers=encabezados,
                                json={"ok": True, "camaras": [{"ip": "192.168.1.51", "nombre": "Prueba"}]})
        assert resultado.json()["aceptada"] and cliente.get(f"{api}/{buscar}").json()["camaras"][0]["ip"] == "192.168.1.51"

        datos = {**formulario, "accion": "agregar", "camara": camara, "nombre": "Camara de alta", "tipo": "tapo",
                 "ip": "192.168.1.51", "usuario_camara": "prueba", "clave_camara": "clave-de-ensayo"}
        primera = cliente.post(api, data=datos)
        assert primera.status_code == 202, primera.text
        alta = primera.json()["id"]
        assert cliente.post(api, data=datos).status_code == 409
        pedido = cliente.post(nodo + "/tomar", headers=encabezados).json()["solicitud"]
        assert pedido["id"] == alta and "clave-de-ensayo" in pedido["perfil"]["rtsp"]
        assert "clave-de-ensayo" not in cliente.get(f"{api}/{alta}").text
        respuesta = cliente.post(f"{nodo}/{alta}/resultado", headers=encabezados, json={"ok": False, "mensaje": "Prueba RTSP fallida."})
        assert respuesta.json() == {"aceptada": False}
        assert cliente.get(f"{api}/{alta}").json()["estado"] == "error"
        with conectar(config) as conexion:
            assert not conexion.execute("SELECT 1 FROM camaras WHERE vivero_id = %s", (sitio,)).fetchone()

        alta = cliente.post(api, data=datos).json()["id"]
        assert cliente.post(nodo + "/tomar", headers=encabezados).json()["solicitud"]["id"] == alta
        fecha = datetime.now(UTC)
        for _ in range(2):
            registro = cliente.post(f"{nodo}/{alta}/resultado", headers=encabezados, json={"ok": True, "foto": nombre(fecha)})
            assert registro.json() == {"aceptada": True}
        assert "miniatura" not in cliente.get(f"{api}/{alta}").json()
        assert subir(cliente, sitio, camara, fecha, ejemplo(1234), token).status_code == 200
        assert importar(config)["nuevas"] == 1
        prueba = cliente.get(f"{api}/{alta}").json()
        assert prueba["estado"] == "ok" and cliente.get(prueba["miniatura"]).status_code == 200
        assert cliente.post(api, data=datos).status_code == 409
        assert cliente.get(f"/viveros/{sitio}").status_code == 200
        comprobar_segunda_zero(cliente, config, sitio, csrf, token)
    print("Alta probada: busqueda, reintento, registro idempotente, primera foto y proteccion de puestos.")


def comprobar_segunda_zero(cliente, config, sitio, csrf, token_primera):
    datos = dict(csrf=csrf, vivero=sitio, nombre="Zero del fondo")
    for _ in range(2):
        creada = cliente.post("/equipos-zero", data=datos, follow_redirects=False)
        assert creada.status_code == 303, creada.text
        assert creada.headers["location"] == f"/equipos-zero?vivero={sitio}#zero-{sitio}.zero02"

    with conectar(config) as conexion:
        assert conexion.execute(
            "SELECT count(*) AS cantidad FROM nodos WHERE vivero_id = %s", (sitio,),
        ).fetchone()["cantidad"] == 2
    vinculacion = cliente.post(f"/viveros/{sitio}/nodos/zero02/credencial", data={"csrf": csrf})
    assert vinculacion.status_code == 200 and "Vincular Zero del fondo" in vinculacion.text
    entrada = re.search(r'<textarea id="credencial"[^>]*>([^<]+)</textarea>', vinculacion.text)[1]
    credencial = json.loads(unescape(entrada))
    assert credencial["nodo"] == "zero02" and credencial["vivero"] == sitio

    cliente.post(f"/viveros/{sitio}/plantilla", data=dict(csrf=csrf, nodo=NODO))
    enc = {"Authorization": f"Bearer {credencial['token']}"}
    base_nodo = f"/api/nodos/{sitio}/zero02"
    respuesta = cliente.post(base_nodo + "/altas/tomar", headers=enc)
    assert respuesta.json() == {"solicitud": None}

    equipos = cliente.get(f"/equipos-zero?vivero={sitio}")
    assert equipos.status_code == 200 and "Lista para agregar cámaras" in equipos.text
    assert f"/viveros/{sitio}/alta-camara?nodo=zero02" in equipos.text
    alta = cliente.get(f"/viveros/{sitio}/alta-camara?nodo=zero02")
    assert alta.status_code == 200 and 'data-nodo="zero02"' in alta.text
    assert 'data-nodo="zero01"' not in alta.text
    assert "H1-C02 · libre de la plantilla" in alta.text
    assert 'value="H1-C01"' not in alta.text

    formulario = dict(csrf=csrf, nodo="zero02", accion="agregar", tipo="tapo", ip="192.168.1.52",
                      usuario_camara="prueba", clave_camara="clave-de-ensayo")
    url = f"/api/viveros/{sitio}/altas"
    assert cliente.post(url, data={**formulario, "camara": "H1-C01"}).status_code == 409
    solicitud = cliente.post(url, data={**formulario, "camara": "H1-C02"})
    assert solicitud.status_code == 202, solicitud.text
    codigo = solicitud.json()["id"]
    assert cliente.post(base_nodo + "/altas/tomar", headers=enc).json()["solicitud"]["id"] == codigo
    assert cliente.get(f"/viveros/{sitio}/alta-camara?nodo=zero01&solicitud={codigo}").status_code == 200
    assert f'data-solicitud="{codigo}"' not in cliente.get(
        f"/viveros/{sitio}/alta-camara?nodo=zero01&solicitud={codigo}",
    ).text
    fecha = datetime.now(UTC)
    respuesta = cliente.post(f"{base_nodo}/altas/{codigo}/resultado", headers=enc,
                             json={"ok": True, "foto": nombre(fecha)})
    assert respuesta.json() == {"aceptada": True}
    assert subir(cliente, sitio, "H1-C02", fecha, ejemplo(1235), token_primera).status_code == 403
    with conectar(config) as conexion:
        asignadas = conexion.execute(
            "SELECT id, nodo_id FROM camaras WHERE vivero_id = %s AND id IN ('H1-C01', 'H1-C02') ORDER BY id",
            (sitio,),
        ).fetchall()
        assert [r["nodo_id"] for r in asignadas] == ["zero01", "zero02"]
        fuentes = conexion.execute(
            "SELECT nodo_id FROM camaras_origen WHERE vivero_id = %s AND camara_id = 'H1-C02'", (sitio,),
        ).fetchall()
        assert fuentes == [{"nodo_id": "zero02"}]


def main():
    ruta = ruta_configuracion()
    valores = json.loads(ruta.read_text(encoding="utf-8"))
    db = valores["base_datos"]
    if db.get("host", "127.0.0.1") not in ("127.0.0.1", "localhost"):
        raise SystemExit("La prueba usa PostgreSQL local.")
    nombre_prueba = f"mgv_prueba_{secrets.token_hex(6)}"
    admin_pg = ["sudo", "-u", "postgres", "--"]
    puerto = str(db.get("puerto", 5432))
    subprocess.run([*admin_pg, "createdb", "-p", puerto, "-O", db["usuario"], nombre_prueba], check=True)
    try:
        with TemporaryDirectory(prefix="mgv-carga-") as carpeta:
            base = Path(carpeta)
            valores["base_datos"]["nombre"] = nombre_prueba
            valores.update(archivo=str(base / "archivo"), min_libre_gib=0.1, viveros=[
                {"id": "piloto", "nombre": "Vivero piloto", "nodos": [{"id": NODO, "entrada": str(base / "entrada"),
                    "camaras": [{"id": CAMARA, "nombre": CAMARA, "hilera": "1"}]}]},
            ])
            Path(valores["archivo"]).mkdir()
            config_temporal = base / "config.json"
            config_temporal.write_text(json.dumps(valores), encoding="utf-8")
            config_temporal.chmod(0o600)
            os.environ["MGV_CENTRAL_CONFIG"] = str(config_temporal)
            comprobar(cargar_configuracion())
    finally:
        subprocess.run([*admin_pg, "dropdb", "-p", puerto, "--if-exists", nombre_prueba], check=True)


if __name__ == "__main__":
    os.umask(0o077)
    sys.exit(main())
