"""API limitada a imágenes: sin ejecución de comandos ni acceso a URL externas."""
import base64
import binascii
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import secrets
from threading import Lock

from fastapi import FastAPI, HTTPException, Request
from PIL import Image, UnidentifiedImageError
from starlette.concurrency import run_in_threadpool

from .motor import ALGORITMO, region_valida, segmentar

app = FastAPI(title="MicroGreenVision IA", docs_url=None, redoc_url=None, openapi_url=None)
MAX_BODY = 4*1024*1024
MAX_IMAGE = 2*1024*1024
Image.MAX_IMAGE_PIXELS = 16_000_000
_en_curso = Lock()


def autorizar(peticion):
    ruta = Path(os.getenv("MGV_IA_TOKEN_FILE", "/workspace/microgreen/servicio_ia/token.txt"))
    try:
        clave = ruta.read_text().strip()
    except OSError:
        raise HTTPException(503, "Falta configurar el acceso al analizador.") from None
    if not 32 <= len(clave) <= 256:
        raise HTTPException(503, "Acceso del analizador sin configurar.")
    recibido = peticion.headers.get("authorization", "")
    if not secrets.compare_digest(recibido.encode(), ("Bearer "+clave).encode()):
        raise HTTPException(401, "Acceso no autorizado.")


@app.get("/salud")
def salud(peticion: Request):
    autorizar(peticion)
    return dict(estado="ok", protocolo=1, algoritmo=ALGORITMO)


def ejecutar(datos):
    try:
        clave = datos["idempotencia"]
        if not isinstance(clave, str) or not re.fullmatch(r"[a-f0-9]{64}", clave):
            raise ValueError("Solicitud inválida.")
        roi = region_valida(datos["roi"])
        contenido = base64.b64decode(datos["imagen"], validate=True)
        if not 0 < len(contenido) <= MAX_IMAGE:
            raise ValueError("La imagen supera el límite.")
        huella = hashlib.sha256(contenido).hexdigest()
        firma = hashlib.sha256(contenido+json.dumps(roi).encode()+ALGORITMO.encode()).hexdigest()
        cache = Path(os.getenv("MGV_IA_CACHE", "/workspace/microgreen/cache_ia"))
        cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        archivo = cache/(clave+".json")
        if archivo.exists():
            anterior = json.loads(archivo.read_text())
            if anterior["firma"] != firma:
                raise HTTPException(409, "La solicitud ya existe con otros datos.")
            return anterior["respuesta"]
        # Caché acotada: reintentos recientes, no archivo histórico de imágenes.
        archivos = sorted(cache.glob('*.json'), key=lambda p:p.stat().st_mtime)
        for viejo in archivos[:-255]:
            viejo.unlink(missing_ok=True)
        with Image.open(BytesIO(contenido)) as imagen:
            if imagen.format not in ("JPEG", "PNG") or imagen.width*imagen.height > 16_000_000:
                raise ValueError("Formato o dimensiones no admitidos.")
            imagen.load()
            resultado, png = segmentar(imagen, roi)
        respuesta = dict(protocolo=1, idempotencia=clave, imagen_sha256=huella,
                         **resultado, vista_png=base64.b64encode(png).decode("ascii"))
        temporal = archivo.with_suffix("."+secrets.token_hex(6)+".tmp")
        try:
            with temporal.open("x") as f:
                os.chmod(temporal, 0o600)
                json.dump(dict(firma=firma, respuesta=respuesta), f)
            temporal.replace(archivo)
        finally:
            temporal.unlink(missing_ok=True)
        return respuesta
    except HTTPException:
        raise
    except (ValueError, TypeError, KeyError, binascii.Error, UnidentifiedImageError, Image.DecompressionBombError):
        raise HTTPException(422, "Imagen, región o solicitud no admitidas. Usá una foto RGB a color y bien iluminada.") from None
    except Exception:
        # No devolver rutas, variables de entorno, secretos ni trazas internas.
        raise HTTPException(503, "El modelo no pudo completar el análisis. Revisá el entorno y los pesos en el Pod.") from None


@app.post("/v1/analizar")
async def analizar(peticion: Request):
    autorizar(peticion)
    contenido = bytearray()
    async for bloque in peticion.stream():
        contenido.extend(bloque)
        if len(contenido) > MAX_BODY:
            raise HTTPException(413, "La solicitud supera el tamaño máximo.")
    try:
        datos = json.loads(contenido)
    except (ValueError, UnicodeError):
        raise HTTPException(422, "Solicitud JSON inválida.") from None
    if not isinstance(datos, dict):
        raise HTTPException(422, "Solicitud JSON inválida.")
    if not _en_curso.acquire(blocking=False):
        raise HTTPException(429, "El analizador está ocupado. Reintentá más tarde.")
    try:
        return await run_in_threadpool(ejecutar, datos)
    finally:
        _en_curso.release()
