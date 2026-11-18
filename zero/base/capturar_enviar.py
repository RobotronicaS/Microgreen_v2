"""Captura JPG de cámaras RTSP y envía los archivos pendientes por SSH."""

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
from datetime import datetime, timezone
from urllib.parse import urlsplit


CAPTURE_TIMEOUT = 30
SEND_TIMEOUT = 180
MIN_FREE_BYTES = 256 * 1024 * 1024
ID_CAMARA = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}")
PUESTOS = tuple(f"H{hilera}-C{puesto:02d}" for hilera in range(1, 4) for puesto in range(1, 6))


class CicloOcupado(Exception):
    pass


def reportar(mensaje):
    print(mensaje, flush=True)


def fecha_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def leer_estado(carpeta):
    try:
        datos = json.loads((carpeta / "estado.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        datos = {}
    if not isinstance(datos, dict):
        datos = {}
    return {
        clave: datos[clave] if isinstance(datos.get(clave), dict) else {}
        for clave in ("camaras", "envio", "ciclo")
    }


def guardar_json(ruta, datos):
    temporal = ruta.with_suffix(".json.tmp")
    try:
        temporal.write_text(json.dumps(datos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporal.replace(ruta)
    finally:
        temporal.unlink(missing_ok=True)


def guardar_estado(carpeta, estado):
    guardar_json(carpeta / "estado.json", estado)


def url_rtsp(valor, identificador):
    try:
        if isinstance(valor, str) and not re.search(r"[\s\x00-\x1f\x7f]", valor):
            url = urlsplit(valor)
            if url.scheme == "rtsp" and url.hostname and url.port != 0 and not url.fragment:
                return url
    except ValueError:
        pass
    raise ValueError(f"{identificador} necesita una URL rtsp:// válida.")


def opciones_captura(camara):
    transporte = camara.get("transporte", "tcp")
    rotacion = camara.get("rotacion", 0)
    ancho = camara.get("ancho", 0)
    espera = camara.get("espera", CAPTURE_TIMEOUT)
    if (transporte not in ("tcp", "udp") or type(rotacion) is not int or rotacion not in (0, 90, 180, 270)
            or type(ancho) is not int or ancho not in (0, 640, 1280, 1920)
            or type(espera) is not int or not 5 <= espera <= 60):
        raise ValueError("Opciones de captura inválidas.")
    filtros = {0: [], 90: ["transpose=1"], 180: ["hflip", "vflip"], 270: ["transpose=2"]}[rotacion]
    if ancho:
        filtros.append(f"scale=w='min({ancho},iw)':h=-2")
    return transporte, filtros, espera


def cargar_config(ruta):
    config = json.loads(ruta.read_text(encoding="utf-8-sig"))
    camaras = config["camaras"]
    if not isinstance(camaras, list):
        raise ValueError("El campo camaras debe ser una lista.")

    ids = set()
    for camara in camaras:
        identificador = camara["id"]
        if not isinstance(identificador, str) or not ID_CAMARA.fullmatch(identificador):
            raise ValueError("Usá identificadores de cámara como H1-C01.")
        if identificador in ids:
            raise ValueError("Cada cámara necesita un identificador distinto.")
        ids.add(identificador)
        url_rtsp(camara["rtsp"], identificador)
        opciones_captura(camara)
        if not isinstance(camara.get("nombre", ""), str):
            raise ValueError(f"El nombre de {identificador} debe ser texto.")

    if not isinstance(config.setdefault("destino", {}), dict):
        raise ValueError("El campo destino debe ser un objeto.")
    return config


def camaras_activas(config):
    return [
        camara for camara in config["camaras"]
        if camara["rtsp"] != "rtsp://USUARIO:CLAVE@192.168.0.60:554/stream1"
    ]


def destino_habilitado(destino):
    if "habilitado" in destino:
        return destino["habilitado"] is True
    return bool(destino.get("ssh")) and (
        destino.get("ssh"), destino.get("directorio"), destino.get("clave_ssh")
    ) != ("capturas@100.64.0.10", "microgreen-inbox/zero01", "~/.ssh/microgreen_zero_ed25519")


def validar_destino(destino):
    ssh = destino.get("ssh", "")
    directorio = destino.get("directorio", "")
    if not isinstance(ssh, str) or not re.fullmatch(r"[a-z_][a-z0-9_.-]*@[A-Za-z0-9][A-Za-z0-9.-]*", ssh):
        raise ValueError("destino.ssh debe tener el formato usuario@equipo.")
    if not isinstance(directorio, str) or not re.fullmatch(
        r"/?[A-Za-z0-9][A-Za-z0-9_.-]*(/[A-Za-z0-9][A-Za-z0-9_.-]*)*", directorio
    ):
        raise ValueError("Usá una ruta de destino como microgreen-inbox/zero01.")
    if not isinstance(destino.get("clave_ssh", ""), str):
        raise ValueError("destino.clave_ssh debe ser una ruta de archivo.")


def bloquear_ciclo(carpeta):
    import fcntl

    bloqueo = (carpeta / ".captura.lock").open("a", encoding="utf-8")
    try:
        fcntl.flock(bloqueo, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        bloqueo.close()
        raise CicloOcupado("Ya hay una captura o un envío en curso.") from error
    except OSError:
        bloqueo.close()
        raise
    return bloqueo


def capturar(camara, cola):
    identificador = camara["id"]
    momento = datetime.now(timezone.utc)
    carpeta = cola / identificador / momento.strftime("%Y-%m-%d")
    carpeta.mkdir(parents=True, exist_ok=True)
    jpg = carpeta / momento.strftime("%Y%m%dT%H%M%S_%fZ.jpg")
    temporal = jpg.with_suffix(".part")
    transporte, filtros, espera = opciones_captura(camara)
    comando = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-rtsp_transport", transporte, "-threads", "1", "-i", camara["rtsp"],
        *(["-vf", ",".join(filtros)] if filtros else []),
        "-map", "0:v:0", "-frames:v", "1", "-threads", "1", "-q:v", "2",
        "-f", "image2", "-update", "1", str(temporal),
    ]
    try:
        resultado = subprocess.run(
            comando, timeout=espera,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        if resultado.returncode or not temporal.is_file() or temporal.stat().st_size < 4:
            diagnostico = resultado.stderr.lower()
            if b"401 unauthorized" in diagnostico or b"403 forbidden" in diagnostico:
                return None, "La cámara rechazó el acceso. Revisá la cuenta de cámara."
            if b"404 not found" in diagnostico:
                return None, "La cámara respondió. Revisá la ruta del perfil RTSP."
            if b"connection refused" in diagnostico:
                return None, "Puerto RTSP cerrado. Revisá el puerto y si RTSP está habilitado."
            return None, "No llegó un JPG. Revisá la IP, el Wi-Fi y la URL RTSP."
        with temporal.open("rb") as archivo:
            inicio = archivo.read(2)
            archivo.seek(-2, os.SEEK_END)
            fin = archivo.read(2)
        if inicio != b"\xff\xd8" or fin != b"\xff\xd9":
            return None, "La captura JPG quedó incompleta."
        temporal.replace(jpg)
        return jpg, "JPG guardado."
    except subprocess.TimeoutExpired:
        return None, f"La cámara no respondió con un JPG en {espera} s."
    except OSError:
        return None, "Revisá FFmpeg y los permisos de escritura."
    finally:
        temporal.unlink(missing_ok=True)


def capturar_todas(camaras, cola, estado):
    carpeta = cola.parent
    vistas = carpeta / "ultimas"
    vistas.mkdir(exist_ok=True)
    completo = True
    for camara in camaras:
        identificador = camara["id"]
        ficha = estado["camaras"].setdefault(identificador, {})
        if shutil.disk_usage(cola).free < MIN_FREE_BYTES:
            ficha.update(estado="pausada", hora=fecha_utc(),
                         detalle="Quedan menos de 256 MiB libres.")
            completo = False
        else:
            ficha.update(estado="capturando", hora=fecha_utc(),
                         detalle="Abriendo el video de la cámara.")
            guardar_estado(carpeta, estado)
            jpg, detalle = capturar(camara, cola)
            if jpg is None:
                ficha.update(estado="error", hora=fecha_utc(), detalle=detalle)
                completo = False
            else:
                ficha.update(estado="ok", hora=fecha_utc(), detalle=detalle)
                vista_temporal = vistas / f"{identificador}.part"
                try:
                    shutil.copyfile(jpg, vista_temporal)
                    vista_temporal.replace(vistas / f"{identificador}.jpg")
                    ficha["ultimo_ok"] = fecha_utc()
                except OSError:
                    ficha["detalle"] = "JPG en cola. Falló la actualización de la vista."
                finally:
                    vista_temporal.unlink(missing_ok=True)
                reportar(f"Capturada {jpg.relative_to(cola)} ({jpg.stat().st_size} bytes).")
        if ficha["estado"] != "ok":
            reportar(f"{identificador}: {ficha['detalle']}")
        estado["ciclo"]["hechas"] += 1
        guardar_estado(carpeta, estado)
    return completo


def enviar(destino, cola, estado):
    carpeta = cola.parent
    envio = estado["envio"]
    if not destino_habilitado(destino):
        envio.update(estado="local", hora=fecha_utc(), detalle="Capturas guardadas en la Zero.")
        guardar_estado(carpeta, estado)
        reportar(envio["detalle"])
        return True
    try:
        validar_destino(destino)
    except ValueError as error:
        envio.update(estado="error", hora=fecha_utc(), detalle=str(error))
        guardar_estado(carpeta, estado)
        reportar(envio["detalle"])
        return False
    pendientes = sum(1 for _ in cola.glob("*/*/*.jpg"))
    if pendientes == 0:
        envio.update(estado="sin pendientes", hora=fecha_utc(), detalle="Cola de envío vacía.")
        guardar_estado(carpeta, estado)
        reportar("Cola vacía.")
        return True

    envio.update(estado="enviando", hora=fecha_utc(),
                 detalle=f"Copiando {pendientes} JPG al receptor.")
    guardar_estado(carpeta, estado)
    ssh = [
        "ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
        "-o", "ConnectTimeout=8", "-o", "ServerAliveInterval=10",
        "-o", "ServerAliveCountMax=3",
    ]
    clave = destino.get("clave_ssh")
    if clave:
        ssh.extend(["-i", str(Path(clave).expanduser()), "-o", "IdentitiesOnly=yes"])
    comando = [
        "rsync", "--recursive", "--times", "--remove-source-files",
        "--prune-empty-dirs", "--timeout=30", "--itemize-changes",
        "--include=*/", "--include=*.jpg", "--exclude=*",
        "-e", shlex.join(ssh), "--", f"{cola}/",
        f"{destino['ssh']}:{destino['directorio']}/",
    ]
    reportar(f"Enviando {pendientes} JPG pendientes.")
    try:
        resultado = subprocess.run(comando, timeout=SEND_TIMEOUT)
        if resultado.returncode == 0:
            envio.update(estado="ok", hora=fecha_utc(), ultimo_ok=fecha_utc(),
                         detalle=f"{pendientes} JPG confirmados.")
            guardar_estado(carpeta, estado)
            reportar("Envío completado.")
            return True
        detalle = f"rsync devolvió código {resultado.returncode}. Reintento pendiente."
    except subprocess.TimeoutExpired:
        detalle = "Tiempo de envío agotado. Reintento pendiente."
    except OSError:
        detalle = "No se pudo ejecutar rsync. Revisá que esté instalado."
    envio.update(estado="error", hora=fecha_utc(), detalle=detalle)
    guardar_estado(carpeta, estado)
    reportar(detalle)
    return False


def seleccionar_camaras(config, identificador=None):
    camaras = camaras_activas(config)
    if identificador is not None:
        camaras = [camara for camara in camaras if camara["id"] == identificador]
        if not camaras:
            raise ValueError("Ese puesto todavía no tiene una cámara configurada.")
    return camaras


def ejecutar_modo(config, carpeta, modo, camaras):
    cola = carpeta / "pendientes"
    cola.mkdir(exist_ok=True)
    estado = leer_estado(carpeta)
    ciclo = {
        "estado": "en curso", "modo": modo, "inicio": fecha_utc(),
        "total": len(camaras) if modo != "enviar" else 0, "hechas": 0,
    }
    estado["ciclo"] = ciclo
    guardar_estado(carpeta, estado)
    ok = False
    try:
        capturas_ok = modo == "enviar" or capturar_todas(camaras, cola, estado)
        envio_ok = modo == "capturar" or enviar(config["destino"], cola, estado)
        ok = capturas_ok and envio_ok
        return 0 if ok else 1
    finally:
        ciclo.update(estado="completo" if ok else "con incidencias", fin=fecha_utc())
        guardar_estado(carpeta, estado)


def main():

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("modo", nargs="?", default="ciclo",
                        choices=["capturar", "enviar", "ciclo"])
    parser.add_argument("--config", type=Path,
                        default=Path(__file__).resolve().with_name("config.json"))
    parser.add_argument("--camara", metavar="ID", help="Puesto individual, por ejemplo H1-C01")
    opciones = parser.parse_args()
    if opciones.camara and opciones.modo != "capturar":
        parser.error("--camara se usa con el modo capturar")
    os.umask(0o077)
    ruta = opciones.config.resolve()
    try:
        with bloquear_ciclo(ruta.parent):
            config = cargar_config(ruta)
            camaras = seleccionar_camaras(config, opciones.camara)
            if opciones.modo == "capturar" and not camaras:
                raise ValueError("Registrá al menos una cámara para probar la captura.")
            return ejecutar_modo(config, ruta.parent, opciones.modo, camaras)
    except CicloOcupado as error:
        reportar(str(error))
        return 1
    except OSError:
        reportar("Revisá config.json y los permisos de la carpeta de MicroGreenVision.")
        return 2
    except ValueError as error:
        reportar(str(error))
        return 2
    except (TypeError, KeyError, AttributeError):
        reportar("La estructura de config.json debe seguir config.ejemplo.json.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

