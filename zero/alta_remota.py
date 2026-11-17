"""Busca y prueba cámaras por pedido de Central, desde la red del invernáculo."""

from ipaddress import IPv4Address
import json
import re
import shutil
import subprocess
from urllib.error import HTTPError
from urllib.parse import urlsplit

from capturar_enviar import (
    ID_CAMARA, MIN_FREE_BYTES, bloquear_ciclo, capturar, cargar_config,
    fecha_utc, guardar_estado, guardar_json, leer_estado, url_rtsp,
)
from descubrir import buscar_camaras

ID_ALTA = re.compile(r"[a-f0-9]{32}")
NOMBRE_JPG = re.compile(r"\d{8}T\d{6}_\d{6}Z\.jpg")


def flujo(url):
    partes = urlsplit(url)
    return partes.hostname, partes.port or 554, partes.path, partes.query


def validar_red(url):
    ip = IPv4Address(urlsplit(url).hostname or "")
    try:
        salida = subprocess.run(
            ["ip", "-j", "-4", "route", "get", str(ip)], check=True, capture_output=True, timeout=3,
        )
        ruta = json.loads(salida.stdout)[0]
        interfaz = ruta.get("dev", "")
        if (not ip.is_loopback and not ip.is_multicast and not ruta.get("gateway")
                and interfaz.startswith(("wl", "eth", "en"))):
            return
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        pass
    raise ValueError("La IP de la cámara debe estar en la red local de la Zero.")


def configurar(raiz, camara):
    config = cargar_config(raiz / "config.json")
    anterior = next((c for c in config["camaras"] if c["id"] == camara["id"]), None)
    if anterior and flujo(anterior["rtsp"]) != flujo(camara["rtsp"]):
        raise ValueError("Ese puesto ya tiene otra cámara en la Zero. Elegí otro puesto.")
    repetida = next((c for c in config["camaras"] if c["id"] != camara["id"]
                     and flujo(c["rtsp"]) == flujo(camara["rtsp"])), None)
    if repetida:
        raise ValueError(f"Ese perfil ya está registrado en el puesto {repetida['id']}.")
    # Conserva las opciones locales de un puesto existente y cambia su acceso con el JPG verificado.
    nueva = {**(anterior or {}), **camara, "activa": True}
    config["camaras"] = [c for c in config["camaras"] if c["id"] != camara["id"]] + [nueva]
    return config


def detalle_local(ruta, camara, foto):
    if not ID_CAMARA.fullmatch(camara) or not NOMBRE_JPG.fullmatch(foto):
        raise ValueError("Datos de la foto de prueba inválidos.")
    dia = f"{foto[:4]}-{foto[4:6]}-{foto[6:8]}"
    return ruta / camara / dia / foto


def confirmar(peticion, alta, datos):
    try:
        return peticion(f"/altas/{alta}/resultado", json.dumps(datos).encode("utf-8"))["aceptada"]
    except HTTPError as error:
        if error.code in (404, 410):
            return False
        raise


def eliminar_prueba(foto):
    foto.unlink(missing_ok=True)
    for carpeta in (foto.parent, foto.parent.parent, foto.parent.parent.parent):
        try:
            carpeta.rmdir()
        except OSError:
            break


def finalizar(raiz, peticion, diario, operacion):
    alta, camara, nombre = operacion["id"], operacion["camara"], operacion["foto"]
    if not ID_ALTA.fullmatch(alta):
        raise ValueError("Solicitud de cámara inválida.")
    prueba = detalle_local(raiz / ".pruebas-camara" / alta, camara["id"], nombre)
    cola = detalle_local(raiz / "pendientes", camara["id"], nombre)
    try:
        config = configurar(raiz, camara) if operacion.get("tipo", "agregar") == "agregar" else None
    except ValueError as error:
        confirmar(peticion, alta, {"ok": False, "mensaje": str(error)})
        eliminar_prueba(prueba)
        diario.unlink(missing_ok=True)
        return
    if not confirmar(peticion, alta, {"ok": True, "foto": nombre}):
        eliminar_prueba(prueba)
        diario.unlink(missing_ok=True)
        return
    if config is not None:
        guardar_json(raiz / "config.json", config)
    if prueba.is_file():
        cola.parent.mkdir(parents=True, exist_ok=True)
        prueba.replace(cola)
        eliminar_prueba(prueba)
    if cola.is_file():
        vistas = raiz / "ultimas"
        vistas.mkdir(exist_ok=True)
        vista = vistas / f"{camara['id']}.jpg"
        temporal = vista.with_suffix(".part")
        try:
            shutil.copyfile(cola, temporal)
            temporal.replace(vista)
        except OSError:
            print("JPG de alta en cola. No se pudo actualizar la vista local.", flush=True)
        finally:
            temporal.unlink(missing_ok=True)
        estado = leer_estado(raiz)
        estado["camaras"].setdefault(camara["id"], {}).update(
            estado="ok", hora=fecha_utc(), detalle="Captura solicitada guardada.", ultimo_ok=fecha_utc(),
        )
        guardar_estado(raiz, estado)
    diario.unlink(missing_ok=True)
    return cola if cola.is_file() else None


def probar(raiz, peticion, diario, solicitud):
    alta = solicitud["id"]
    codigo = solicitud["camara_id"]
    perfil = solicitud["perfil"]
    if not ID_CAMARA.fullmatch(codigo):
        raise ValueError("Puesto inválido.")
    url = perfil["rtsp"]
    camara = {"id": codigo, "nombre": solicitud["nombre"], "tipo": perfil["tipo"], "rtsp": url}
    with bloquear_ciclo(raiz):
        try:
            url_rtsp(url, codigo)
            configurar(raiz, camara)
            validar_red(camara["rtsp"])
            if shutil.disk_usage(raiz).free < MIN_FREE_BYTES:
                raise ValueError("La Zero tiene menos de 256 MiB libres.")
        except ValueError as error:
            confirmar(peticion, alta, {"ok": False, "mensaje": str(error)})
            return
        carpeta = raiz / ".pruebas-camara" / alta
        foto = next((p for p in carpeta.glob(f"{codigo}/*/*.jpg")
                     if p.is_file() and not p.is_symlink() and NOMBRE_JPG.fullmatch(p.name)), None)
        if foto is None:
            foto, detalle = capturar(camara, carpeta)
            if foto is None:
                confirmar(peticion, alta, {"ok": False, "mensaje": detalle})
                return
        operacion = {"id": alta, "camara": camara, "foto": foto.name}
        guardar_json(diario, operacion)
        return finalizar(raiz, peticion, diario, operacion)


def atender_altas(raiz, peticion):
    diario = raiz / "alta_camara.json"
    if diario.is_file():
        with bloquear_ciclo(raiz):
            operacion = json.loads(diario.read_text(encoding="utf-8"))
            return finalizar(raiz, peticion, diario, operacion)
    solicitud = peticion("/altas/tomar", b"{}")["solicitud"]
    if not solicitud:
        return
    alta = solicitud["id"]
    if not ID_ALTA.fullmatch(alta):
        raise ValueError("Solicitud de cámara inválida.")
    if solicitud["tipo"] == "buscar":
        try:
            datos = buscar_camaras()
            resultado = {"ok": True, "camaras": datos["camaras"]}
        except (OSError, ValueError):
            resultado = {"ok": False, "mensaje": "Revisá que la Zero esté conectada al Wi-Fi del invernáculo."}
        confirmar(peticion, alta, resultado)
        return
    if solicitud["tipo"] == "agregar":
        return probar(raiz, peticion, diario, solicitud)
    if solicitud["tipo"] == "capturar":
        return capturar_actual(raiz, peticion, diario, solicitud)


def capturar_actual(raiz, peticion, diario, solicitud):
    codigo = solicitud["camara_id"]
    if not ID_CAMARA.fullmatch(codigo):
        raise ValueError("Puesto inválido.")
    with bloquear_ciclo(raiz):
        config = cargar_config(raiz / "config.json")
        camara = next((c for c in config["camaras"] if c["id"] == codigo), None)
        if camara is None:
            confirmar(peticion, solicitud["id"], {"ok": False, "mensaje": "La cámara necesita configuración en esta Zero."})
            return
        if shutil.disk_usage(raiz).free < MIN_FREE_BYTES:
            confirmar(peticion, solicitud["id"], {"ok": False, "mensaje": "La Zero tiene menos de 256 MiB libres."})
            return
        carpeta = raiz / ".pruebas-camara" / solicitud["id"]
        foto = next((p for p in carpeta.glob(f"{codigo}/*/*.jpg")
                     if p.is_file() and not p.is_symlink() and NOMBRE_JPG.fullmatch(p.name)), None)
        if foto is None:
            foto, detalle = capturar(camara, carpeta)
            if foto is None:
                confirmar(peticion, solicitud["id"], {"ok": False, "mensaje": detalle})
                return
        operacion = {"tipo": "capturar", "id": solicitud["id"], "camara": {"id": codigo}, "foto": foto.name}
        guardar_json(diario, operacion)
        return finalizar(raiz, peticion, diario, operacion)
