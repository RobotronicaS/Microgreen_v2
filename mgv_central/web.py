from datetime import UTC, datetime
from io import StringIO
from itertools import chain
from pathlib import Path
from urllib.parse import parse_qsl
import csv

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.datastructures import FormData

from . import __version__
from .configuracion import cargar_configuracion
from .dominio import zona

PLANTILLAS = Jinja2Templates(directory=str(Path(__file__).with_name("plantillas")))
LIMITE_FORMULARIO = 600 * 1024


def fecha_local(valor, sitio_zona=None):
    if not valor:
        return "—"
    horario = valor.astimezone(zona(sitio_zona) if sitio_zona else cargar_configuracion().zona)
    return horario.strftime("%d/%m/%Y %H:%M:%S")


def peso(valor):
    if valor is None:
        return "—"
    if valor >= 1024**3:
        return f"{valor / 1024**3:.1f} GiB"
    if valor >= 1024**2:
        return f"{valor / 1024**2:.1f} MiB"
    return f"{valor / 1024:.0f} KiB"


PLANTILLAS.env.filters.update(fecha_local=fecha_local, peso=peso)


def pagina(peticion, plantilla, usuario=None, codigo=200, **contexto):
    return PLANTILLAS.TemplateResponse(
        request=peticion, name=plantilla, status_code=codigo,
        context={"usuario": usuario, "version": __version__, **contexto},
    )


def volver(ruta):
    return RedirectResponse(ruta, status_code=303)


async def datos_formulario(peticion: Request):
    if peticion.headers.get("content-type", "").split(";")[0] != "application/x-www-form-urlencoded":
        raise HTTPException(415, "Formato de formulario inválido.")
    contenido = bytearray()
    async for trozo in peticion.stream():
        if len(contenido) + len(trozo) > LIMITE_FORMULARIO:
            raise HTTPException(413, "El formulario supera el tamaño máximo.")
        contenido.extend(trozo)
    try:
        return FormData(parse_qsl(contenido.decode("utf-8"), keep_blank_values=True, max_num_fields=400))
    except (ValueError, UnicodeError):
        raise HTTPException(400, "No se pudo leer el formulario.") from None


def fila(conexion, consulta, valores, mensaje):
    registro = conexion.execute(consulta, valores).fetchone()
    if registro is None:
        raise HTTPException(404, mensaje)
    return registro


def sitio(conexion, vivero):
    return fila(conexion, "SELECT * FROM viveros WHERE id = %s", (vivero,), "Invernáculo desconocido.")


def puesto(conexion, vivero, camara):
    return fila(conexion, "SELECT * FROM camaras WHERE vivero_id = %s AND id = %s",
                (vivero, camara), "Puesto desconocido.")


def local_hoy(sitio_zona):
    return datetime.now(UTC).astimezone(zona(sitio_zona)).date()


def csv_respuesta(nombre, columnas, filas):
    def generar():
        with StringIO(newline="") as salida:
            escritor = csv.writer(salida)
            yield "\ufeff"
            for valores in chain((columnas,), filas):
                escritor.writerow(valores)
                yield salida.getvalue()
                salida.seek(0)
                salida.truncate()

    return StreamingResponse(
        generar(), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nombre}.csv"'},
    )


def foto_ruta(config, categoria, clave):
    raiz = config.archivo / categoria
    ruta = (raiz / clave).resolve()
    if not ruta.is_relative_to(raiz.resolve()) or not ruta.is_file():
        raise HTTPException(404, "Archivo no disponible.")
    return ruta
