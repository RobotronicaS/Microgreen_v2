"""Conexión opcional al analizador de imágenes de un Pod RunPod."""
import base64
import binascii
import hashlib
from io import BytesIO
import json
from math import isfinite
import os
from pathlib import Path
import re
import secrets
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request as HTTPRequest, build_opener

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field, SecretStr, model_validator
from psycopg.types.json import Jsonb
from starlette.datastructures import FormData

from .acceso import exigir
from .base import auditar, conectar
from .configuracion import cargar_configuracion, ruta_configuracion
from .ingesta import guardar_archivo
from .web import datos_formulario, fila, foto_ruta, pagina, sitio, volver, csv_respuesta

rutas = APIRouter()
ALGORITMO = "sam2.1-tiny-verde-v1"
MAX_RESPUESTA = 4*1024*1024


class ErrorIA(OSError):
    pass


class AjustesIA(BaseModel):
    activo: bool = False
    automatico: bool = False
    url: str = ""
    token: SecretStr = SecretStr("")
    espera: int = Field(default=45, ge=5, le=60)

    @model_validator(mode="after")
    def validar(self):
        self.url = self.url.strip().rstrip("/")
        if self.url and not re.fullmatch(r"https://[a-z0-9]+-[0-9]{2,5}\.proxy\.runpod\.net", self.url):
            raise ValueError("Usá la dirección HTTPS del puerto del analizador en RunPod, sin rutas ni claves en la URL.")
        clave = self.token.get_secret_value()
        if clave and (not 32 <= len(clave) <= 256 or not clave.isascii() or any(c.isspace() for c in clave)):
            raise ValueError("La clave de análisis debe tener 32 a 256 caracteres, sin espacios.")
        if self.activo and (not self.url or not clave):
            raise ValueError("Indicá dirección y clave para activar el análisis remoto.")
        return self


def archivo_ajustes():
    return ruta_configuracion().with_name("runpod.json")


def cargar_ajustes():
    try:
        return AjustesIA.model_validate_json(archivo_ajustes().read_text())
    except FileNotFoundError:
        return AjustesIA()
    except (OSError, ValueError):
        raise ErrorIA("Revisá la configuración de RunPod en Central.") from None


def guardar_ajustes(ajustes):
    ruta = archivo_ajustes()
    temporal = ruta.with_name(".runpod-"+secrets.token_hex(8)+".tmp")
    datos = ajustes.model_dump(mode="json")
    datos["token"] = ajustes.token.get_secret_value()
    try:
        with temporal.open("x") as f:
            os.chmod(temporal, 0o600)
            json.dump(datos, f)
        temporal.replace(ruta)
    finally:
        temporal.unlink(missing_ok=True)


class SinRedireccion(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def llamada(ajustes, ruta, datos=None):
    cuerpo = None if datos is None else json.dumps(datos).encode()
    peticion = HTTPRequest(ajustes.url+ruta, data=cuerpo, headers={
        "Authorization": "Bearer "+ajustes.token.get_secret_value(),
        "Content-Type": "application/json", "Accept": "application/json",
    })
    try:
        with build_opener(SinRedireccion()).open(peticion, timeout=ajustes.espera) as respuesta:
            contenido = respuesta.read(MAX_RESPUESTA+1)
        if len(contenido) > MAX_RESPUESTA:
            raise ErrorIA("El analizador devolvió una respuesta demasiado grande.")
        resultado = json.loads(contenido)
        if not isinstance(resultado, dict):
            raise ValueError
        return resultado
    except HTTPError as e:
        mensajes = {401:"La clave del analizador fue rechazada.", 422:"La foto no es apta: verificá color, iluminación y región.",
                    409:"El identificador del análisis no coincide con la imagen.", 413:"La imagen supera el tamaño admitido.",
                    429:"El analizador está ocupado. Se reintentará."}
        raise ErrorIA(mensajes.get(e.code, "El analizador no está disponible. Revisá que el Pod y la API estén encendidos.")) from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise ErrorIA("No se pudo completar la conexión con el analizador. Revisá el Pod, su URL y el acceso.") from None


def programar(conexion, usuario_id, vivero, captura, lote, revision):
    return conexion.execute(
        """INSERT INTO trabajos(vivero_id, usuario_id, tipo, clave, datos)
        VALUES (%s,%s,'ia',%s,%s) ON CONFLICT(clave) DO UPDATE SET estado='pendiente',
          disponible_en=now(), intentos=0, mensaje='' WHERE trabajos.estado IN ('error','listo') RETURNING id""",
        (vivero, usuario_id, f"ia:{captura}:{lote}:{revision}", Jsonb(dict(captura=captura,lote=lote,revision=revision))),
    ).fetchone()


def automatico(conexion, foto, lotes):
    ajustes = cargar_ajustes()
    if ajustes.activo and ajustes.automatico:
        for lote in lotes:
            clave = f"ia:{foto['id']}:{lote['id']}:{lote['revision']}"
            if not conexion.execute("SELECT 1 FROM trabajos WHERE clave=%s", (clave,)).fetchone():
                programar(conexion, None, foto['vivero_id'], foto['id'], lote['id'], lote['revision'])


def validar_resultado(resultado, solicitud, contenido, roi):
    try:
        cobertura = resultado["cobertura"]
        if (resultado["protocolo"] != 1 or resultado["algoritmo"] != ALGORITMO
            or resultado["validacion"] not in ('estimacion','insuficiente')
            or resultado["idempotencia"] != solicitud or resultado["imagen_sha256"] != hashlib.sha256(contenido).hexdigest()
            or not isinstance(cobertura,(int,float)) or isinstance(cobertura,bool) or not isfinite(cobertura) or not 0 <= cobertura <= 100
            or len(resultado["roi"]) != 4 or any(not isfinite(float(a)) or abs(float(a)-float(b)) > 1e-6 for a,b in zip(resultado["roi"],roi))):
            raise ValueError
        png = base64.b64decode(resultado["vista_png"], validate=True)
        if len(png) > 2*1024*1024:
            raise ValueError
        with Image.open(BytesIO(png)) as imagen:
            if imagen.format != "PNG" or imagen.width > 1280 or imagen.height > 1280:
                raise ValueError
            imagen.verify()
        return float(cobertura), png
    except (KeyError, TypeError, ValueError, binascii.Error, UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ErrorIA("La respuesta del analizador no coincide con la foto o tiene un formato inválido.") from None


def analizar_ia(conexion, config, trabajo):
    from .procesos import TrabajoInvalido, jpg_original
    ajustes = cargar_ajustes()
    if not ajustes.activo:
        raise TrabajoInvalido("RunPod está desactivado en Configuración → Análisis con IA.")
    datos = trabajo['datos']
    registro = conexion.execute(
        """SELECT f.*, l.roi, l.revision, (f.capturada_en AT TIME ZONE v.zona)::date AS dia_lote
        FROM capturas f JOIN viveros v ON v.id=f.vivero_id JOIN lotes l ON l.vivero_id=f.vivero_id
        AND l.camara_id=f.camara_id WHERE f.id=%s AND f.vivero_id=%s AND l.id=%s
        AND (f.capturada_en AT TIME ZONE v.zona)::date >= l.siembra
        AND (l.cosecha IS NULL OR (f.capturada_en AT TIME ZONE v.zona)::date <= l.cosecha)""",
        (datos['captura'],trabajo['vivero_id'],datos['lote']),
    ).fetchone()
    if not registro or registro['revision'] != datos['revision']:
        raise TrabajoInvalido("La región o el lote cambiaron. Solicitá un análisis nuevo.")
    with Image.open(jpg_original(config, registro['clave_archivo'])) as original:
        imagen = ImageOps.exif_transpose(original).convert('RGB')
        imagen.thumbnail((1280,1280))
        salida = BytesIO(); imagen.save(salida, 'JPEG', quality=85)
        contenido = salida.getvalue()
    if len(contenido) > 2*1024*1024:
        raise TrabajoInvalido("La foto preparada supera el tamaño admitido.")
    roi = registro['roi']
    solicitud = hashlib.sha256(contenido+json.dumps(roi).encode()+ALGORITMO.encode()).hexdigest()
    resultado = llamada(ajustes, '/v1/analizar', dict(idempotencia=solicitud,imagen=base64.b64encode(contenido).decode(),roi=roi))
    cobertura, png = validar_resultado(resultado, solicitud, contenido, roi)
    clave = f"{registro['vivero_id']}/{datos['lote']}/r{datos['revision']}/{datos['captura']}.png"
    with conexion.transaction():
        lote = conexion.execute("SELECT revision,siembra,cosecha FROM lotes WHERE id=%s FOR UPDATE",(datos['lote'],)).fetchone()
        if (not lote or lote['revision'] != datos['revision'] or registro['dia_lote'] < lote['siembra']
                or (lote['cosecha'] and registro['dia_lote'] > lote['cosecha'])):
            raise TrabajoInvalido("La región cambió durante el análisis. Solicitá uno nuevo.")
        guardar_archivo(config.archivo/'ia'/clave, png)
        conexion.execute(
            """INSERT INTO resultados_ia(captura_id,lote_id,revision,algoritmo,cobertura,mascara,solicitud,validacion)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(captura_id,lote_id,revision) DO UPDATE SET
            algoritmo=EXCLUDED.algoritmo,cobertura=EXCLUDED.cobertura,mascara=EXCLUDED.mascara,
            solicitud=EXCLUDED.solicitud,validacion=EXCLUDED.validacion,medida_en=now()""",
            (datos['captura'],datos['lote'],datos['revision'],ALGORITMO,cobertura,clave,solicitud,resultado['validacion']),
        )
    mensaje = ('SAM2: segmentación insuficiente. Revisá la región; el porcentaje no es una medición válida.'
               if resultado['validacion']=='insuficiente' else f"SAM2 · {cobertura:.1f}% de cobertura estimada. Revisá la imagen marcada.")
    return mensaje, None


@rutas.get('/runpod')
def configuracion(peticion: Request):
    with conectar(cargar_configuracion()) as c:
        usuario = exigir(c,peticion,admin=True)
    ajustes = cargar_ajustes()
    return pagina(peticion,'runpod.html',usuario,titulo='Análisis con IA',ajustes=ajustes,
                  con_clave=bool(ajustes.token.get_secret_value()), mensaje=peticion.query_params.get('mensaje',''))


@rutas.post('/runpod')
def configurar(peticion: Request, datos: FormData=Depends(datos_formulario)):
    with conectar(cargar_configuracion()) as c:
        usuario = exigir(c,peticion,admin=True,formulario=datos)
        anterior = cargar_ajustes()
        try:
            ajustes = AjustesIA(activo=datos.get('activo')=='si',automatico=datos.get('automatico')=='si',
                               url=datos.get('url',''),token=datos.get('token') or anterior.token,
                               espera=datos.get('espera','45'))
        except ValueError:
            raise ValueError('Revisá la dirección HTTPS de RunPod, la clave (32 a 256 caracteres sin espacios) y la espera (5 a 60 s).') from None
        guardar_ajustes(ajustes)
        auditar(c,usuario,None,'configurar analisis RunPod',f'activo={ajustes.activo}; automatico={ajustes.automatico}')
    return volver('/runpod?mensaje=guardado')


@rutas.post('/runpod/probar')
def probar(peticion: Request, datos: FormData=Depends(datos_formulario)):
    with conectar(cargar_configuracion()) as c:
        usuario = exigir(c,peticion,admin=True,formulario=datos)
        ajustes = cargar_ajustes()
        if not ajustes.url or not ajustes.token.get_secret_value():
            raise ValueError('Guardá la dirección y la clave del analizador primero.')
        try:
            estado = llamada(ajustes,'/salud')
            if estado.get('protocolo') != 1 or estado.get('algoritmo') != ALGORITMO or estado.get('estado') != 'ok':
                raise ErrorIA('El servicio no utiliza el protocolo esperado.')
        except ErrorIA as e:
            return pagina(peticion,'runpod.html',usuario,codigo=502,titulo='Análisis con IA',ajustes=ajustes,
                          con_clave=True,error=str(e))
    return volver('/runpod?mensaje=conectado')


@rutas.get('/runpod/paquete')
def paquete(peticion: Request):
    with conectar(cargar_configuracion()) as c:
        exigir(c,peticion,admin=True)
    ruta = Path(__file__).resolve().parent.parent/'runpod-ia.tar.gz'
    if not ruta.is_file():
        raise HTTPException(404,'Reinstalá Central desde las fuentes completas para preparar el paquete del Pod.')
    return FileResponse(ruta,filename='microgreen-runpod-ia.tar.gz',media_type='application/gzip')


def lote_accesible(c, peticion, lote, editar=False, datos=None):
    registro = fila(c,'SELECT * FROM lotes WHERE id=%s',(lote,),'Lote desconocido.')
    usuario = exigir(c,peticion,registro['vivero_id'],editar=editar,formulario=datos)
    return registro, usuario


@rutas.post('/lotes/{lote}/runpod')
def solicitar(peticion: Request,lote: int,datos: FormData=Depends(datos_formulario)):
    with conectar(cargar_configuracion()) as c:
        registro,usuario = lote_accesible(c,peticion,lote,True,datos)
        if not cargar_ajustes().activo:
            raise ValueError('Activá el analizador en Configuración → Análisis con IA.')
        captura = c.execute(
            """SELECT f.id FROM capturas f JOIN viveros v ON v.id=f.vivero_id
            WHERE f.vivero_id=%s AND f.camara_id=%s AND (f.capturada_en AT TIME ZONE v.zona)::date >= %s
            AND (%s::date IS NULL OR (f.capturada_en AT TIME ZONE v.zona)::date <= %s)
            ORDER BY f.capturada_en DESC LIMIT 1""",
            (registro['vivero_id'],registro['camara_id'],registro['siembra'],registro['cosecha'],registro['cosecha']),
        ).fetchone()
        if not captura:
            raise ValueError('Este lote todavía no tiene fotos dentro de sus fechas.')
        programar(c,usuario['id'],registro['vivero_id'],captura['id'],lote,registro['revision'])
        auditar(c,usuario,registro['vivero_id'],'solicitar analisis RunPod',f"lote={lote}; captura={captura['id']}")
    return volver(f'/lotes/{lote}/runpod')


FILTRO = """FROM resultados_ia r JOIN capturas f ON f.id=r.captura_id JOIN lotes l ON l.id=r.lote_id
JOIN viveros v ON v.id=f.vivero_id WHERE r.lote_id=%s AND r.revision=%s
AND (f.capturada_en AT TIME ZONE v.zona)::date >= l.siembra
AND (l.cosecha IS NULL OR (f.capturada_en AT TIME ZONE v.zona)::date <= l.cosecha)"""
CONSULTA = "SELECT r.*, f.capturada_en "+FILTRO+" ORDER BY f.capturada_en DESC"


@rutas.get('/lotes/{lote}/runpod')
def resultados(peticion: Request,lote: int):
    with conectar(cargar_configuracion()) as c:
        registro,usuario = lote_accesible(c,peticion,lote)
        resultados = c.execute(CONSULTA+" LIMIT 100",(lote,registro['revision'])).fetchall()
        trabajos = c.execute("""SELECT id,estado,mensaje,creado_en FROM trabajos WHERE vivero_id=%s AND tipo='ia'
                             AND (datos->>'lote')::bigint=%s AND (datos->>'revision')::integer=%s
                             ORDER BY id DESC LIMIT 10""",(registro['vivero_id'],lote,registro['revision'])).fetchall()
        vivero = sitio(c,registro['vivero_id'])
    return pagina(peticion,'runpod_resultados.html',usuario,titulo='Análisis del lote',lote=registro,sitio=vivero,
                  resultados=resultados,trabajos=trabajos)


@rutas.get('/lotes/{lote}/runpod/{captura}.png')
def mascara(peticion: Request,lote: int,captura: int):
    config = cargar_configuracion()
    with conectar(config) as c:
        registro,_ = lote_accesible(c,peticion,lote)
        r = fila(c,'SELECT r.mascara '+FILTRO+' AND r.captura_id=%s',
                 (lote,registro['revision'],captura),'Análisis no disponible para esta región.')
    return FileResponse(foto_ruta(config,'ia',r['mascara']),media_type='image/png')


@rutas.get('/lotes/{lote}/runpod.csv')
def exportar(peticion: Request,lote: int):
    with conectar(cargar_configuracion()) as c:
        registro,_ = lote_accesible(c,peticion,lote)
        valores = c.execute(CONSULTA,(lote,registro['revision'])).fetchall()
    return csv_respuesta(f'ia-lote-{lote}.csv',['captura','fecha_utc','revision','algoritmo','validacion','cobertura_pct'],
                         [(r['captura_id'],r['capturada_en'].isoformat(),r['revision'],r['algoritmo'],r['validacion'],
                           r['cobertura'] if r['validacion']=='estimacion' else None) for r in valores])
