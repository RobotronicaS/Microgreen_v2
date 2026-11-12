"""Panel local de MicroGreenVision Zero v2, accesible mediante SSH."""

import argparse
from collections import Counter
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import re
from threading import Lock, Thread
from urllib.parse import quote, unquote, urlsplit

from capturar_enviar import (
    CicloOcupado, ID_CAMARA, PUESTOS, bloquear_ciclo, camaras_activas, cargar_config,
    destino_habilitado, ejecutar_modo, guardar_estado, guardar_json, leer_estado,
    reportar, seleccionar_camaras, url_rtsp,
)
from descubrir import buscar_camaras


CARPETA_APP = Path(__file__).resolve().parent
ESTATICOS = {
    "/": ("panel.html", "text/html; charset=utf-8"),
    "/panel.css": ("panel.css", "text/css; charset=utf-8"),
    "/panel.js": ("panel.js", "text/javascript; charset=utf-8"),
}
RUTA_FOTO = re.compile(rf"/foto/({ID_CAMARA.pattern})\.jpg")
HOST_LOCAL = re.compile(r"(?:127\.0\.0\.1|localhost)(?::[0-9]{1,5})?")


def tipo_camara(camara, url):
    tipo = camara.get("tipo")
    if tipo in ("tapo", "rtsp"):
        return tipo
    if url.path in ("/stream1", "/stream2") and url.port in (None, 554) and not url.query:
        try:
            ipaddress.IPv4Address(url.hostname)
            return "tapo"
        except ValueError:
            pass
    return "rtsp"


def hay_ciclo(carpeta):
    try:
        with bloquear_ciclo(carpeta):
            return False
    except CicloOcupado:
        return True


def datos_panel(ruta_config):
    carpeta = ruta_config.parent
    ocupado = hay_ciclo(carpeta)
    estado = leer_estado(carpeta)
    cola = carpeta / "pendientes"
    pendientes = Counter(jpg.parent.parent.name for jpg in cola.glob("*/*/*.jpg"))
    mensaje = ""
    try:
        config = cargar_config(ruta_config)
    except FileNotFoundError:
        config = {"camaras": [], "destino": {}}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        config = {"camaras": [], "destino": {}}
        mensaje = "Revisá el formato de config.json en la Zero."

    camaras = {camara["id"]: camara for camara in camaras_activas(config)}
    filas = []
    for identificador in dict.fromkeys((*PUESTOS, *camaras)):
        camara = camaras.get(identificador)
        ficha = {
            "id": identificador, "nombre": "", "host": "", "tipo": "tapo", "perfil": "stream2",
            "configurada": camara is not None, "foto": None, "hora_foto": None,
            "pendientes": pendientes[identificador], "estado": "sin configurar", "detalle": "",
        }
        if camara:
            url = url_rtsp(camara["rtsp"], identificador)
            ficha.update(nombre=camara.get("nombre", ""), host=url.hostname,
                         tipo=tipo_camara(camara, url))
            if url.path == "/stream1":
                ficha["perfil"] = "stream1"
            lectura = estado["camaras"].get(identificador)
            if not isinstance(lectura, dict):
                lectura = {}
            ficha.update(estado=lectura.get("estado", "pendiente"), detalle=lectura.get("detalle", ""))
            if ficha["estado"] == "capturando" and not ocupado:
                ficha.update(estado="interrumpida", detalle="La captura anterior se interrumpió.")
            vista = carpeta / "ultimas" / f"{identificador}.jpg"
            try:
                datos = vista.stat()
                ficha.update(foto=str(datos.st_mtime_ns), hora_foto=datetime.fromtimestamp(
                    datos.st_mtime, timezone.utc).isoformat(timespec="seconds"))
            except FileNotFoundError:
                pass
        filas.append(ficha)

    ciclo = estado["ciclo"]
    envio = estado["envio"]
    if not ocupado and ciclo.get("estado") == "en curso":
        ciclo["estado"] = "interrumpido"
    if not destino_habilitado(config["destino"]):
        envio = {"estado": "local", "detalle": "Capturas guardadas en la Zero."}
    elif not ocupado and envio.get("estado") == "enviando":
        envio.update(estado="interrumpido", detalle="El próximo ciclo reintentará el envío.")
    return {"camaras": filas, "envio": envio, "ciclo": ciclo, "ocupado": ocupado,
            "pendientes": sum(pendientes.values()), "mensaje": mensaje}


def texto(datos, clave, limite):
    valor = datos.get(clave, "")
    if not isinstance(valor, str) or len(valor) > limite:
        raise ValueError(f"Revisá el campo {clave}.")
    return valor


def guardar_camara(ruta, datos):
    identificador = texto(datos, "id", 32).strip()
    if not ID_CAMARA.fullmatch(identificador):
        raise ValueError("Ingresá un identificador como H1-C01.")
    nombre = texto(datos, "nombre", 80).strip()
    tipo = texto(datos, "tipo", 8)
    if tipo not in ("tapo", "rtsp"):
        raise ValueError("Elegí un tipo de cámara.")

    with bloquear_ciclo(ruta.parent):
        try:
            config = cargar_config(ruta)
        except FileNotFoundError:
            config = {"camaras": [], "destino": {}}
        anterior = next((cam for cam in camaras_activas(config) if cam["id"] == identificador), {})
        rtsp_anterior = anterior.get("rtsp", "")
        if tipo == "tapo":
            try:
                host = str(ipaddress.IPv4Address(texto(datos, "ip", 15).strip()))
            except ValueError as error:
                raise ValueError("Ingresá la IPv4 de la Tapo, por ejemplo 192.168.1.42.") from error
            perfil = texto(datos, "perfil", 7)
            if perfil not in ("stream1", "stream2"):
                raise ValueError("Elegí el perfil RTSP de la Tapo.")
            usuario = texto(datos, "usuario", 128).strip()
            clave = texto(datos, "clave", 256)
            if rtsp_anterior:
                url = url_rtsp(rtsp_anterior, identificador)
                if tipo_camara(anterior, url) == "tapo":
                    usuario_anterior = unquote(url.username or "")
                    usuario = usuario or usuario_anterior
                    if usuario == usuario_anterior:
                        clave = clave or unquote(url.password or "")
            if not usuario or not clave:
                raise ValueError("Ingresá el usuario y la contraseña de la Cuenta de cámara Tapo.")
            rtsp = f"rtsp://{quote(usuario, safe='')}:{quote(clave, safe='')}@{host}:554/{perfil}"
        else:
            rtsp = texto(datos, "rtsp", 4096).strip() or rtsp_anterior
        url_rtsp(rtsp, identificador)

        nueva = {**anterior, "id": identificador, "nombre": nombre, "tipo": tipo, "rtsp": rtsp}
        camaras = {cam["id"]: cam for cam in config["camaras"]}
        camaras[identificador] = nueva
        config["camaras"] = list(camaras.values())
        guardar_json(ruta, config)
        if rtsp != rtsp_anterior:
            (ruta.parent / "ultimas" / f"{identificador}.jpg").unlink(missing_ok=True)
            estado = leer_estado(ruta.parent)
            estado["camaras"].pop(identificador, None)
            guardar_estado(ruta.parent, estado)


def iniciar_captura(ruta, identificador):
    bloqueo = bloquear_ciclo(ruta.parent)

    def capturar():
        with bloqueo:
            try:
                ejecutar_modo(config, ruta.parent, "capturar", camaras)
            except (OSError, ValueError):
                reportar("Falló la captura. Revisá el espacio y los permisos de la Zero.")

    try:
        config = cargar_config(ruta)
        camaras = seleccionar_camaras(config, identificador)
        if not camaras:
            raise ValueError("Registrá al menos una cámara.")
        Thread(target=capturar, daemon=True).start()
    except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError):
        bloqueo.close()
        raise
    return len(camaras)


class PanelHandler(BaseHTTPRequestHandler):
    def log_message(self, formato, *args):
        pass

    def responder(self, contenido, tipo, estado=200):
        self.send_response(estado)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(contenido)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'; form-action 'self'")
        self.end_headers()
        try:
            self.wfile.write(contenido)
        except ConnectionError:
            pass

    def responder_json(self, datos, estado=200):
        contenido = json.dumps(datos, ensure_ascii=False).encode("utf-8")
        self.responder(contenido, "application/json; charset=utf-8", estado)

    def do_GET(self):
        if not HOST_LOCAL.fullmatch(self.headers.get("Host", "")):
            self.send_error(403)
            return
        ruta = self.path.split("?", 1)[0]
        config = self.server.config
        try:
            if ruta in ESTATICOS:
                nombre, tipo = ESTATICOS[ruta]
                self.responder((CARPETA_APP / nombre).read_bytes(), tipo)
            elif ruta == "/api/estado":
                self.responder_json(datos_panel(config))
            elif coincidencia := RUTA_FOTO.fullmatch(ruta):
                vista = config.parent / "ultimas" / f"{coincidencia[1]}.jpg"
                self.responder(vista.read_bytes(), "image/jpeg")
            else:
                self.send_error(404)
        except FileNotFoundError:
            self.send_error(404)
        except ConnectionError:
            pass
        except OSError:
            self.send_error(503)

    def leer_peticion(self):
        if self.headers.get_content_type() != "application/json":
            raise ValueError("Se requiere una solicitud JSON.")
        longitud = int(self.headers.get("Content-Length", "0"))
        if not 0 < longitud <= 8192:
            raise ValueError("Solicitud incompleta o demasiado grande.")
        self.connection.settimeout(6)
        datos = json.loads(self.rfile.read(longitud))
        if not isinstance(datos, dict):
            raise ValueError("La solicitud debe ser un objeto.")
        return datos

    def do_POST(self):
        host = self.headers.get("Host", "")
        if not HOST_LOCAL.fullmatch(host) or self.headers.get("Origin") != f"http://{host}":
            self.responder_json({"error": "Abrí el panel por su túnel SSH local."}, 403)
            return
        try:
            datos = self.leer_peticion()
            if self.path == "/api/camaras":
                guardar_camara(self.server.config, datos)
                self.responder_json({"mensaje": "Cámara guardada."})
            elif self.path == "/api/capturar":
                identificador = texto(datos, "id", 32) or None
                cantidad = iniciar_captura(self.server.config, identificador)
                self.responder_json({"mensaje": f"Inició la captura de {cantidad} cámara(s)."}, 202)
            elif self.path == "/api/buscar":
                if not self.server.busqueda.acquire(blocking=False):
                    self.responder_json({"error": "Ya hay una búsqueda Wi-Fi en curso."}, 409)
                    return
                try:
                    self.responder_json(buscar_camaras(self.server.interfaz))
                finally:
                    self.server.busqueda.release()
            else:
                self.responder_json({"error": "Ruta desconocida."}, 404)
        except CicloOcupado as error:
            self.responder_json({"error": str(error)}, 409)
        except FileNotFoundError:
            self.responder_json({"error": "Registrá la primera cámara."}, 400)
        except (TypeError, KeyError, AttributeError, json.JSONDecodeError):
            self.responder_json({"error": "Revisá la estructura de la solicitud o de config.json."}, 400)
        except ValueError as error:
            self.responder_json({"error": str(error)}, 400)
        except OSError:
            self.responder_json({"error": "Revisá el Wi-Fi o los permisos de la Zero."}, 503)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CARPETA_APP / "config.json")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--interfaz", default="wlan0", help="Interfaz para buscar cámaras ONVIF")
    args = parser.parse_args()
    os.umask(0o077)
    with ThreadingHTTPServer(("127.0.0.1", args.port), PanelHandler) as servidor:
        servidor.config = args.config.resolve()
        servidor.interfaz = args.interfaz
        servidor.busqueda = Lock()
        print(f"Panel en http://127.0.0.1:{servidor.server_port}", flush=True)
        try:
            servidor.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
