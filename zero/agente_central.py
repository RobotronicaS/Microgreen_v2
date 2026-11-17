"""Captura local de la Zero y entrega autenticada a MicroGreenVision Central."""
import argparse
from datetime import datetime, time, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
from time import monotonic
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from zoneinfo import ZoneInfo

from alta_remota import atender_altas
from capturar_enviar import (
    CicloOcupado, ID_CAMARA, bloquear_ciclo, camaras_activas, cargar_config, ejecutar_modo,
    fecha_utc, guardar_estado, guardar_json, leer_estado,
)

RAIZ = Path(__file__).resolve().parent
COLA = RAIZ / "pendientes"
NOMBRE = re.compile(r"\d{8}T\d{6}_\d{6}Z\.jpg")
POLITICA = RAIZ / "central_politica.json"
TURNOS = RAIZ / "central_turnos.json"
INTERVALOS = (15, 30, 60, 120, 240)


class SinRedireccion(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def leer_json(ruta, defecto=None):
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return defecto


def datos_central():
    datos = leer_json(RAIZ / "central.json")
    if not isinstance(datos, dict):
        raise ValueError("Creá central.json con la credencial emitida desde Central.")
    url = urlsplit(datos["url"])
    if (url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment
            or not ID_CAMARA.fullmatch(datos["vivero"]) or not ID_CAMARA.fullmatch(datos["nodo"])
            or not re.fullmatch(r"[A-Za-z0-9_-]{43}", datos["token"])):
        raise ValueError("Revisá URL HTTPS, invernáculo, nodo y credencial en central.json.")
    return datos


def llamar(datos, ruta, contenido=None, tipo="application/json"):
    destino = datos["url"].rstrip("/") + f"/api/nodos/{datos['vivero']}/{datos['nodo']}" + ruta
    cabeceras = {"Authorization": f"Bearer {datos['token']}", "Accept": "application/json"}
    if contenido is not None:
        cabeceras["Content-Type"] = tipo
    peticion = Request(destino, data=contenido, headers=cabeceras)
    with build_opener(SinRedireccion(), ProxyHandler({})).open(peticion, timeout=6) as respuesta:
        return json.loads(respuesta.read(256 * 1024))


def politica_valida(datos):
    if not isinstance(datos, dict) or not isinstance(datos.get("camaras"), list):
        raise ValueError("La política de Central es inválida.")
    ZoneInfo(datos["zona"])
    for camara in datos["camaras"]:
        if not ID_CAMARA.fullmatch(camara["id"]) or camara["intervalo"] not in INTERVALOS:
            raise ValueError("El puesto o intervalo de Central es inválido.")
        time.fromisoformat(camara["desde"])
        time.fromisoformat(camara["hasta"])
    return datos


def actualizar_politica(datos, en_linea=True):
    try:
        politica = politica_valida(llamar(datos, "/config"))
    except (OSError, ValueError, KeyError, TypeError):
        if en_linea:
            raise
        return politica_valida(leer_json(POLITICA, {"zona": "America/Montevideo", "camaras": []}))
    guardar_json(POLITICA, politica)
    return politica


def en_horario(hora, desde, hasta):
    if desde == hasta:
        return True
    return desde <= hora < hasta if desde < hasta else hora >= desde or hora < hasta


def ejecutar_captura(datos):
    politica = actualizar_politica(datos, en_linea=False)
    with bloquear_ciclo(RAIZ):
        config = cargar_config(RAIZ / "config.json")
        guardadas = {c["id"]: c for c in camaras_activas(config)}
        marcados = leer_json(TURNOS, {})
        ahora = datetime.now(timezone.utc)
        zona = ZoneInfo(politica["zona"])
        elegidas = []
        for camara in politica["camaras"]:
            id_camara = camara["id"]
            if not camara["activa"] or id_camara not in guardadas:
                continue
            paso = camara["intervalo"] * 60
            turno = int(ahora.timestamp()) // paso * paso
            hora = datetime.fromtimestamp(turno, zona).time()
            clave = f"{turno}:{paso}"
            if en_horario(hora, time.fromisoformat(camara["desde"]), time.fromisoformat(camara["hasta"])) and marcados.get(id_camara) != clave:
                elegidas.append((guardadas[id_camara], clave))
        if not elegidas:
            return
        ejecutar_modo(config, RAIZ, "capturar", [c for c, _ in elegidas])
        for camara, clave in elegidas:
            marcados[camara["id"]] = clave
        guardar_json(TURNOS, marcados)


def pendientes():
    return sorted(
        (p for p in COLA.glob("*/*/*.jpg") if p.is_file() and not p.is_symlink() and NOMBRE.fullmatch(p.name)
         and ID_CAMARA.fullmatch(p.parent.parent.name)),
        key=lambda p: (p.name, p.parent.parent.name),
    )


def informe(lista, envio):
    locales = {c["id"] for c in camaras_activas(cargar_config(RAIZ / "config.json"))}
    estado = leer_estado(RAIZ)["camaras"]
    politica = leer_json(POLITICA, {"camaras": []})
    camaras = {}
    for registro in politica["camaras"]:
        camara = registro["id"]
        if camara not in locales:
            camaras[camara] = "sin_configurar"
        elif not registro["activa"]:
            camaras[camara] = "pausada"
        else:
            ultimo = estado.get(camara, {}).get("estado", "sin_configurar")
            camaras[camara] = ultimo if ultimo in ("ok", "error", "pausada") else "sin_configurar"
    antigua = datetime.strptime(lista[0].name, "%Y%m%dT%H%M%S_%fZ.jpg").replace(tzinfo=timezone.utc).isoformat() if lista else None
    return {"libres_bytes": shutil.disk_usage(RAIZ).free, "pendientes": len(lista),
            "mas_antigua": antigua, "camaras": camaras, "envio": envio, "capacidades": ["capturar"]}


def entregar_jpg(datos, foto):
    jpg = foto.read_bytes()
    camara = foto.parent.parent.name
    resultado = llamar(datos, f"/fotos/{camara}/{foto.name}", jpg, "image/jpeg")
    if not resultado.get("recibida") or resultado.get("sha256") != sha256(jpg).hexdigest():
        raise OSError("Central devolvi\u00f3 un acuse inv\u00e1lido.")
    foto.unlink()
    print(f"Recibida en Central: {camara}/{foto.name}", flush=True)


def enviar(datos):
    with bloquear_ciclo(RAIZ):
        cola = pendientes()
        recientes = {p.parent.parent.name: p for p in cola}
        prioridad = list(dict.fromkeys([*recientes.values(), *cola]))
        salida = "ok"
        inicio = monotonic()
        for foto in prioridad[:36]:
            if monotonic() - inicio > 25:
                break
            try:
                entregar_jpg(datos, foto)
            except HTTPError as error:
                salida = "error"
                print(f"{foto.name}: respuesta HTTP {error.code}. JPG conservado.", flush=True)
                if error.code not in (400, 403, 409, 413, 415):
                    break
            except (OSError, ValueError):
                salida = "error"
                print("Central sin respuesta válida. Se conserva la cola.", flush=True)
                break
        quedan = pendientes()
        estado = leer_estado(RAIZ)
        estado["envio"].update(estado=salida, hora=fecha_utc(), detalle=f"{len(quedan)} JPG pendientes hacia Central.")
        if salida == "ok":
            estado["envio"]["ultimo_ok"] = fecha_utc()
        guardar_estado(RAIZ, estado)
        llamar(datos, "/estado", json.dumps(informe(quedan, salida)).encode("utf-8"))


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("modo", choices=("probar", "capturar", "enviar", "altas"))
    modo = parser.parse_args().modo
    try:
        datos = datos_central()
        if modo == "probar":
            politica = actualizar_politica(datos)
            llamar(datos, "/estado", json.dumps(informe(pendientes(), "ok")).encode("utf-8"))
            print(f"Conexión confirmada. {len(politica['camaras'])} puestos registrados en Central.", flush=True)
        elif modo == "capturar":
            ejecutar_captura(datos)
        elif modo == "altas":
            foto = atender_altas(RAIZ, lambda ruta, contenido: llamar(datos, ruta, contenido))
            if foto:
                with bloquear_ciclo(RAIZ):
                    if foto.is_file():
                        entregar_jpg(datos, foto)
        else:
            enviar(datos)
    except CicloOcupado:
        print("Captura o envío en curso. Se retoma con el próximo temporizador.", flush=True)
    except (OSError, ValueError, KeyError, TypeError) as error:
        detalle = f" HTTP {error.code}." if isinstance(error, HTTPError) else ""
        print(f"Revisá la conexión privada, central.json y el catálogo de cámaras.{detalle}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
