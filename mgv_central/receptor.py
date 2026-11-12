from datetime import UTC, datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
import shutil
from tempfile import NamedTemporaryFile

from fastapi import APIRouter, HTTPException, Request
from psycopg.types.json import Jsonb
from starlette.concurrency import run_in_threadpool

from .acceso import huella
from .base import conectar
from .configuracion import cargar_configuracion
from .dominio import ID, guardar_sensores
from .ingesta import JPG, crear_bandeja, sincronizar, verificar_huella

api = APIRouter(prefix="/api/nodos")


def autenticar(conexion, peticion, vivero, nodo):
    if not ID.fullmatch(vivero) or not ID.fullmatch(nodo):
        raise HTTPException(404, "Nodo desconocido.")
    auth = peticion.headers.get("authorization", "")
    if not auth.startswith("Bearer ") or len(auth) != 50 or not auth.isascii():
        raise HTTPException(401, "Credencial de nodo requerida.")
    fila = conexion.execute(
        """SELECT n.token_sha, v.zona FROM nodos n JOIN viveros v ON v.id = n.vivero_id
        WHERE n.vivero_id = %s AND n.id = %s""",
        (vivero, nodo),
    ).fetchone()
    if not fila or not fila["token_sha"] or not secrets.compare_digest(huella(auth[7:]), fila["token_sha"]):
        raise HTTPException(401, "Credencial de nodo inválida.")
    return fila


def camara_autorizada(config, peticion, vivero, nodo, camara):
    with conectar(config) as conexion:
        autenticar(conexion, peticion, vivero, nodo)
        if not conexion.execute(
            "SELECT 1 FROM camaras WHERE vivero_id = %s AND nodo_id = %s AND id = %s",
            (vivero, nodo, camara),
        ).fetchone():
            raise HTTPException(403, "La cámara no pertenece a este nodo.")


async def json_limitado(peticion, limite=128 * 1024):
    contenido = bytearray()
    async for trozo in peticion.stream():
        if len(contenido) + len(trozo) > limite:
            raise HTTPException(413, "Solicitud demasiado grande.")
        contenido.extend(trozo)
    try:
        return json.loads(contenido)
    except (UnicodeError, ValueError):
        raise HTTPException(400, "JSON inválido.") from None


def nombre_jpg(nombre):
    if not JPG.fullmatch(nombre):
        raise HTTPException(400, "Nombre de JPG inválido.")
    try:
        return datetime.strptime(nombre, "%Y%m%dT%H%M%S_%fZ.jpg").replace(tzinfo=UTC)
    except ValueError:
        raise HTTPException(400, "Fecha de captura inválida.") from None


def recibir(config, vivero, nodo, camara, nombre, momento, temporal, sha):
    with conectar(config) as conexion:
        foto = conexion.execute(
            """SELECT sha256, clave_archivo FROM capturas
            WHERE vivero_id = %s AND camara_id = %s AND capturada_en = %s""",
            (vivero, camara, momento),
        ).fetchone()
    if foto:
        if foto["sha256"] != sha:
            raise HTTPException(409, "Ya existe otra foto para ese puesto y hora.")
        if verificar_huella(config.archivo / "originales" / foto["clave_archivo"], sha):
            return {"recibida": True, "duplicada": True, "sha256": sha}
    for _ in range(3):
        carpeta = crear_bandeja(config, vivero, nodo, camara, momento.date().isoformat())
        destino = carpeta / nombre
        try:
            os.link(temporal, destino)
        except FileExistsError:
            try:
                with destino.open("rb") as archivo:
                    existente = sha256(archivo.read(config.max_jpg_mib * 1024 * 1024 + 1)).hexdigest()
            except FileNotFoundError:
                continue
            if existente != sha:
                raise HTTPException(409, "Ya hay otra foto en recepción para ese puesto y hora.")
        except FileNotFoundError:
            continue
        try:
            sincronizar(carpeta)
        except FileNotFoundError:
            continue
        return {"recibida": True, "duplicada": False, "sha256": sha}
    raise HTTPException(503, "La recepción está ocupada. Conservá el JPG y reintentá.")


@api.post("/{vivero}/{nodo}/fotos/{camara}/{nombre}")
async def subir_foto(peticion: Request, vivero: str, nodo: str, camara: str, nombre: str):
    config = cargar_configuracion()
    await run_in_threadpool(camara_autorizada, config, peticion, vivero, nodo, camara)
    momento = nombre_jpg(nombre)
    limite = config.max_jpg_mib * 1024 * 1024
    try:
        tamano = int(peticion.headers.get("content-length", "0"))
    except ValueError:
        tamano = 0
    if not 4 <= tamano <= limite:
        raise HTTPException(413, "Tamaño de JPG fuera del límite configurado.")
    if peticion.headers.get("content-type", "").split(";")[0] != "image/jpeg":
        raise HTTPException(415, "Usá Content-Type image/jpeg.")
    if shutil.disk_usage(config.archivo).free < config.min_libre_gib * 1024**3 + tamano:
        raise HTTPException(507, "El archivo central tiene poco espacio libre.")
    base = config.archivo / "bandeja"
    base.mkdir(exist_ok=True, mode=0o700)
    sincronizar(base.parent)
    cantidad = 0
    digest = sha256()
    temporal = None
    try:
        with NamedTemporaryFile(dir=base, prefix=".recibiendo-", delete=False) as salida:
            temporal = Path(salida.name)
            async for trozo in peticion.stream():
                cantidad += len(trozo)
                if cantidad > tamano:
                    raise HTTPException(413, "La foto supera el tamaño declarado.")
                digest.update(trozo)
                salida.write(trozo)
            if cantidad != tamano:
                raise HTTPException(400, "La foto llegó incompleta.")
            salida.flush()
            os.fsync(salida.fileno())
        return await run_in_threadpool(recibir, config, vivero, nodo, camara, nombre, momento, temporal, digest.hexdigest())
    finally:
        if temporal:
            temporal.unlink(missing_ok=True)


@api.get("/{vivero}/{nodo}/config")
def politica(peticion: Request, vivero: str, nodo: str):
    with conectar(cargar_configuracion()) as conexion:
        registro = autenticar(conexion, peticion, vivero, nodo)
        camaras = conexion.execute(
            """SELECT id, activa, intervalo, desde::text, hasta::text FROM camaras
            WHERE vivero_id = %s AND nodo_id = %s ORDER BY id""",
            (vivero, nodo),
        ).fetchall()
    return {"zona": registro["zona"], "camaras": camaras}


def informe_estado(conexion, vivero, nodo, datos):
    if not isinstance(datos, dict):
        raise ValueError("El estado debe ser un objeto.")
    reporte = {}
    for campo in ("libres_bytes", "pendientes"):
        valor = datos.get(campo)
        if type(valor) is not int or not 0 <= valor <= 2**50:
            raise ValueError("Espacio libre o cantidad pendiente inválida.")
        reporte[campo] = valor
    if datos.get("mas_antigua"):
        fecha = datetime.fromisoformat(datos["mas_antigua"])
        if fecha.tzinfo is None:
            raise ValueError("Fecha de cola sin zona horaria.")
        reporte["mas_antigua"] = fecha.isoformat()
    estados = datos.get("camaras", {})
    if not isinstance(estados, dict) or len(estados) > 150:
        raise ValueError("Estado de cámaras inválido.")
    permitidas = {fila["id"] for fila in conexion.execute(
        "SELECT id FROM camaras WHERE vivero_id = %s AND nodo_id = %s", (vivero, nodo),
    )}
    reporte["camaras"] = {
        clave: valor for clave, valor in estados.items()
        if clave in permitidas and valor in ("ok", "error", "sin_configurar", "pausada")
    }
    reporte["envio"] = "error" if datos.get("envio") == "error" else "ok"
    conexion.execute(
        "UPDATE nodos SET ultimo_contacto = now(), reporte = %s WHERE vivero_id = %s AND id = %s",
        (Jsonb(reporte), vivero, nodo),
    )


def registrar_reporte(config, peticion, vivero, nodo, datos):
    with conectar(config) as conexion:
        autenticar(conexion, peticion, vivero, nodo)
        try:
            informe_estado(conexion, vivero, nodo, datos)
        except (ValueError, TypeError) as error:
            raise HTTPException(400, str(error)) from None
    return {"ok": True}


@api.post("/{vivero}/{nodo}/estado")
async def reportar(peticion: Request, vivero: str, nodo: str):
    return await run_in_threadpool(
        registrar_reporte, cargar_configuracion(), peticion, vivero, nodo, await json_limitado(peticion),
    )


def registrar_lecturas(config, peticion, vivero, nodo, filas):
    with conectar(config) as conexion:
        autenticar(conexion, peticion, vivero, nodo)
        if not isinstance(filas, list) or not 1 <= len(filas) <= 500:
            raise HTTPException(400, "Enviá una lista de 1 a 500 lecturas.")
        try:
            cantidad = guardar_sensores(conexion, vivero, nodo, filas)
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
    return {"incorporadas": cantidad}


@api.post("/{vivero}/{nodo}/sensores")
async def lecturas(peticion: Request, vivero: str, nodo: str):
    return await run_in_threadpool(
        registrar_lecturas, cargar_configuracion(), peticion, vivero, nodo, await json_limitado(peticion),
    )
