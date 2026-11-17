from datetime import UTC, date, datetime, timedelta
from pathlib import Path
import secrets
import shutil

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import psycopg
from psycopg.types.json import Jsonb
from starlette.datastructures import FormData
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .acceso import (
    COOKIE, COOKIE_INGRESO, SEGUNDOS_SESION, abrir_sesion, csrf_valido, exigir, usuario_actual,
)
from .altas import rutas as altas
from .base import auditar, conectar
from .configuracion import cargar_configuracion
from .cultivos import rutas as cultivos
from .dominio import rango_local, zona
from .gestion import rutas as gestion
from .mantenimiento import rutas as mantenimiento
from .receptor import api
from .registro import rutas as registro
from .resumen import captura_disponible, resumen_camaras, viveros_visibles
from .supervisor import estado_supervisor
from .vinculacion import rutas as vinculacion
from .web import datos_formulario, fila, foto_ruta, local_hoy, pagina, puesto, sitio, volver

app = FastAPI(title="MicroGreenVision Central", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=str(Path(__file__).with_name("static"))), name="static")
app.include_router(api)
app.include_router(altas)
app.include_router(gestion)
app.include_router(cultivos)
app.include_router(vinculacion)
app.include_router(mantenimiento)
app.include_router(registro)
POR_PAGINA = 48


@app.middleware("http")
async def cabeceras(peticion, siguiente):
    respuesta = await siguiente(peticion)
    respuesta.headers["X-Content-Type-Options"] = "nosniff"
    respuesta.headers["Referrer-Policy"] = "no-referrer"
    respuesta.headers["X-Frame-Options"] = "DENY"
    respuesta.headers["Cache-Control"] = "no-store"
    respuesta.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
    )
    return respuesta


@app.exception_handler(StarletteHTTPException)
async def error_http(peticion, error):
    if error.status_code == 303:
        return volver(error.headers["Location"])
    if peticion.url.path.startswith("/api/") or peticion.url.path == "/salud":
        return JSONResponse({"detalle": error.detail}, status_code=error.status_code)
    return pagina(peticion, "error.html", codigo=error.status_code, mensaje=error.detail)


@app.exception_handler(ValueError)
async def error_entrada(peticion, error):
    if peticion.url.path.startswith("/api/"):
        return JSONResponse({"detalle": str(error)}, status_code=400)
    return pagina(peticion, "error.html", codigo=400, mensaje=str(error))


@app.exception_handler(psycopg.errors.UniqueViolation)
async def error_duplicado(peticion, error):
    return pagina(peticion, "error.html", codigo=409, mensaje="Ya hay un registro con ese identificador.")


@app.exception_handler(psycopg.Error)
async def error_base(peticion, error):
    mensaje = "PostgreSQL no pudo completar la operación. Revisá el servicio."
    if peticion.url.path.startswith("/api/") or peticion.url.path == "/salud":
        return JSONResponse({"detalle": mensaje}, status_code=503)
    return pagina(peticion, "error.html", codigo=503, mensaje=mensaje)


@app.get("/salud")
def salud():
    with conectar(cargar_configuracion()) as conexion:
        conexion.execute("SELECT 1")
    return {"estado": "ok", "version": __version__}


@app.get("/ingresar")
def ingresar(peticion: Request):
    config = cargar_configuracion()
    with conectar(config) as conexion:
        if usuario_actual(conexion, peticion):
            return volver("/")
    token = secrets.token_urlsafe(32)
    respuesta = pagina(peticion, "ingresar.html", csrf=token)
    respuesta.set_cookie(COOKIE_INGRESO, token, max_age=600, httponly=True, samesite="strict",
                         secure=config.cookies_seguras or peticion.url.scheme == "https")
    return respuesta


@app.post("/ingresar")
def autenticar(peticion: Request, datos: FormData = Depends(datos_formulario)):
    config = cargar_configuracion()
    if not csrf_valido(datos.get("csrf"), peticion.cookies.get(COOKIE_INGRESO)):
        return volver("/ingresar")
    with conectar(config) as conexion:
        token = abrir_sesion(conexion, datos.get("usuario", "").strip().lower(), datos.get("clave", ""),
                             peticion.client.host if peticion.client else "local")
    if not token:
        # El navegador conserva el formulario y recibe un token nuevo.
        nuevo = secrets.token_urlsafe(32)
        respuesta = pagina(peticion, "ingresar.html", codigo=401, csrf=nuevo, fallo=True)
        respuesta.set_cookie(COOKIE_INGRESO, nuevo, max_age=600, httponly=True, samesite="strict",
                             secure=config.cookies_seguras or peticion.url.scheme == "https")
        return respuesta
    respuesta = volver("/")
    respuesta.set_cookie(COOKIE, token, max_age=SEGUNDOS_SESION, httponly=True, samesite="strict",
                         secure=config.cookies_seguras or peticion.url.scheme == "https")
    respuesta.delete_cookie(COOKIE_INGRESO)
    return respuesta


@app.post("/salir")
def salir(peticion: Request, datos: FormData = Depends(datos_formulario)):
    with conectar(cargar_configuracion()) as conexion:
        usuario = exigir(conexion, peticion, formulario=datos)
        conexion.execute("DELETE FROM sesiones WHERE token_sha = %s", (usuario["token_sha"],))
    respuesta = volver("/ingresar")
    respuesta.delete_cookie(COOKIE)
    return respuesta


@app.get("/", name="inicio")
def inicio(peticion: Request, atrasadas: bool = False):
    with conectar(cargar_configuracion()) as conexion:
        usuario = exigir(conexion, peticion)
        sitios = resumen_camaras(conexion, viveros_visibles(conexion, usuario))
        alertas = conexion.execute(
            """SELECT vivero_id, count(*) AS cantidad FROM alertas
            WHERE cerrada_en IS NULL AND vivero_id = ANY(%s) GROUP BY vivero_id""",
            ([v["id"] for v in sitios],),
        ).fetchall()
        importador = conexion.execute("SELECT * FROM estado_importador WHERE id").fetchone()
        avisos_globales = conexion.execute(
            "SELECT count(*) AS n FROM alertas WHERE vivero_id IS NULL AND cerrada_en IS NULL",
        ).fetchone()["n"] if usuario["administrador"] else 0
        registros_pendientes = conexion.execute(
            "SELECT count(*) AS n FROM solicitudes_acceso WHERE estado = 'pendiente'",
        ).fetchone()["n"] if usuario["administrador"] else 0
    cantidades = {a["vivero_id"]: a["cantidad"] for a in alertas}
    for sitio_actual in sitios:
        sitio_actual["alertas"] = cantidades.get(sitio_actual["id"], 0)
    totales = {clave: sum(v[clave] for v in sitios) for clave in ("activas", "aldia", "recibidas", "esperadas")}
    visibles = [s for s in sitios if s["alertas"] or s["atrasadas"]] if atrasadas else sitios
    return pagina(peticion, "inicio.html", usuario, sitios=visibles, cantidad=len(sitios),
                  totales=totales, importador=importador, atrasadas=atrasadas,
                  incidencias=sum(cantidades.values()) + avisos_globales,
                  con_avisos=sum(bool(s["alertas"] or s["atrasadas"]) for s in sitios),
                  registros_pendientes=registros_pendientes, titulo="Invernáculos")


@app.get("/viveros/{vivero_id}", name="vivero")
def vivero(peticion: Request, vivero_id: str):
    with conectar(cargar_configuracion()) as conexion:
        usuario = exigir(conexion, peticion, vivero_id)
        vivero_actual = resumen_camaras(conexion, [sitio(conexion, vivero_id)])[0]
        lotes = conexion.execute(
            "SELECT * FROM lotes WHERE vivero_id = %s ORDER BY siembra DESC, id DESC LIMIT 12", (vivero_id,),
        ).fetchall()
        sensores = conexion.execute(
            """SELECT DISTINCT ON (origen) * FROM sensores WHERE vivero_id = %s ORDER BY origen, fecha DESC""",
            (vivero_id,),
        ).fetchall()
    hileras = {}
    for ficha in vivero_actual["camaras"]:
        hileras.setdefault(ficha["hilera"], []).append(ficha)
    return pagina(peticion, "vivero.html", usuario, sitio=vivero_actual, hileras=hileras, lotes=lotes,
                  sensores=sensores, titulo=vivero_actual["nombre"])


@app.get("/viveros/{vivero_id}/camaras/{camara_id}", name="historial")
def historial(peticion: Request, vivero_id: str, camara_id: str, fecha: str = "", lote: str = "",
              pagina_numero: int = 1, captura: str = ""):
    if not 1 <= pagina_numero <= 100_000:
        raise ValueError("Número de página inválido.")
    lote = int(lote) if lote else None
    anterior_dia = siguiente_dia = None
    with conectar(cargar_configuracion()) as conexion:
        usuario = exigir(conexion, peticion, vivero_id)
        vivero_actual = sitio(conexion, vivero_id)
        camara = puesto(conexion, vivero_id, camara_id)
        equipo_captura = fila(conexion, """SELECT id, reporte, alta_en, token_sha IS NOT NULL AS enlazado
            FROM nodos WHERE vivero_id = %s AND id = %s""", (vivero_id, camara["nodo_id"]), "Zero desconocida.")
        puede_capturar = captura_disponible(equipo_captura, datetime.now(UTC))
        solicitud_captura = conexion.execute(
            """SELECT id FROM altas_camara WHERE id = %s AND vivero_id = %s
            AND camara_id = %s AND tipo = 'capturar'""", (captura, vivero_id, camara_id),
        ).fetchone() if len(captura) == 32 else None
        hoy = local_hoy(vivero_actual["zona"])
        lotes = conexion.execute(
            "SELECT * FROM lotes WHERE vivero_id = %s AND camara_id = %s ORDER BY siembra DESC",
            (vivero_id, camara_id),
        ).fetchall()
        condiciones = ["f.vivero_id = %s", "f.camara_id = %s"]
        parametros = [vivero_id, camara_id]
        if fecha:
            desde, hasta = rango_local(fecha, fecha, zona(vivero_actual["zona"]))
            if desde.date() > date.min:
                anterior_dia = desde.date() - timedelta(days=1)
            if hasta.date() <= hoy:
                siguiente_dia = hasta.date()
            condiciones.append("f.capturada_en >= %s AND f.capturada_en < %s")
            parametros.extend((desde, hasta))
        if lote is not None:
            seleccionado = next((item for item in lotes if item["id"] == lote), None)
            if seleccionado is None:
                raise HTTPException(404, "Lote desconocido para esta cámara.")
            condiciones.append("(f.capturada_en AT TIME ZONE %s)::date BETWEEN %s AND %s")
            parametros.extend((vivero_actual["zona"], seleccionado["siembra"], seleccionado["cosecha"] or hoy))
        fotos = conexion.execute(
            """SELECT f.*, q.problemas, q.revision FROM capturas f
            LEFT JOIN calidad q ON q.captura_id = f.id WHERE """ + " AND ".join(condiciones) +
            " ORDER BY f.capturada_en DESC LIMIT %s OFFSET %s",
            (*parametros, POR_PAGINA + 1, (pagina_numero - 1) * POR_PAGINA),
        ).fetchall()
    return pagina(peticion, "historial.html", usuario, sitio=vivero_actual, camara=camara, fotos=fotos[:POR_PAGINA],
                  lotes=lotes, lote=lote, fecha=fecha, hoy=hoy, pagina=pagina_numero,
                  anterior_dia=anterior_dia, siguiente_dia=siguiente_dia,
                  puede_capturar=puede_capturar, solicitud_captura=solicitud_captura["id"] if solicitud_captura else "",
                  siguiente=len(fotos) > POR_PAGINA, titulo=camara["nombre"])


def fotos_seleccionadas(conexion, vivero, camara, ids):
    if not ids or len(ids) > 200:
        raise ValueError("Seleccioná entre 1 y 200 fotos.")
    fotos = conexion.execute(
        """SELECT * FROM capturas WHERE vivero_id = %s AND camara_id = %s AND id = ANY(%s)
        ORDER BY capturada_en""", (vivero, camara, ids),
    ).fetchall()
    if len(fotos) != len(set(ids)):
        raise HTTPException(404, "Alguna foto no pertenece a esta cámara.")
    return fotos


def crear_trabajo(conexion, usuario, vivero, tipo, datos):
    with conexion.transaction():
        conexion.execute("SELECT pg_advisory_xact_lock(%s)", (usuario["id"] + 431005000,))
        pendientes = conexion.execute(
            """SELECT count(*) AS cantidad FROM trabajos
            WHERE usuario_id = %s AND estado IN ('pendiente', 'en_proceso')""", (usuario["id"],),
        ).fetchone()
        if pendientes["cantidad"] >= 5:
            raise HTTPException(429, "Esperá a que termine alguno de tus trabajos.")
        return conexion.execute(
            """INSERT INTO trabajos (vivero_id, usuario_id, tipo, datos) VALUES (%s, %s, %s, %s) RETURNING id""",
            (vivero, usuario["id"], tipo, Jsonb(datos)),
        ).fetchone()["id"]


@app.post("/viveros/{vivero_id}/camaras/{camara_id}/seleccion")
def seleccion(peticion: Request, vivero_id: str, camara_id: str, datos: FormData = Depends(datos_formulario)):
    with conectar(cargar_configuracion()) as conexion:
        usuario = exigir(conexion, peticion, vivero_id, formulario=datos)
        camara = puesto(conexion, vivero_id, camara_id)
        sitio_actual = sitio(conexion, vivero_id)
        ids = [int(valor) for valor in datos.getlist("fotos")]
        if datos.get("accion") == "comparar":
            if len(ids) != 2 or ids[0] == ids[1]:
                raise ValueError("Marcá dos fotos distintas para compararlas.")
            fotos = fotos_seleccionadas(conexion, vivero_id, camara_id, ids)
            return pagina(peticion, "comparar.html", usuario, sitio=sitio_actual, camara=camara,
                          anterior=fotos[0], posterior=fotos[1], titulo="Comparar capturas")
        if datos.get("accion") == "zip":
            fotos_seleccionadas(conexion, vivero_id, camara_id, ids)
            crear_trabajo(conexion, usuario, vivero_id, "zip", {"camara": camara_id, "fotos": ids})
        elif datos.get("accion") == "timelapse":
            desde, hasta = rango_local(datos.get("desde", ""), datos.get("hasta", ""), zona(sitio_actual["zona"]), 90)
            fps = int(datos.get("fps", "12"))
            if fps not in (6, 12, 24):
                raise ValueError("Elegí 6, 12 o 24 cuadros por segundo.")
            crear_trabajo(conexion, usuario, vivero_id, "timelapse",
                          {"camara": camara_id, "desde": desde.isoformat(), "hasta": hasta.isoformat(), "fps": fps})
        else:
            raise ValueError("Acción de captura desconocida.")
    return volver("/trabajos")


@app.post("/fotos/{foto_id}/revisar")
def revisar_foto(peticion: Request, foto_id: int, datos: FormData = Depends(datos_formulario)):
    valor = datos.get("revision")
    if valor not in ("aceptada", "descartada", "automatica"):
        raise ValueError("Revisión desconocida.")
    with conectar(cargar_configuracion()) as conexion:
        foto = fila(conexion, "SELECT * FROM capturas WHERE id = %s", (foto_id,), "Foto desconocida.")
        usuario = exigir(conexion, peticion, foto["vivero_id"], editar=True, formulario=datos)
        with conexion.transaction():
            cambio = conexion.execute(
                """UPDATE calidad SET revision = %s, revisada_por = %s, revisada_en = now()
                WHERE captura_id = %s RETURNING captura_id""",
                (None if valor == "automatica" else valor, usuario["id"], foto_id),
            ).fetchone()
            if not cambio:
                raise HTTPException(409, "Esperá la evaluación de calidad antes de revisar esta foto.")
            conexion.execute(
                """UPDATE trabajos SET estado = 'pendiente', disponible_en = now(), intentos = 0
                WHERE clave = %s AND estado IN ('listo', 'error')""", (f"foto:{foto_id}",),
            )
            auditar(conexion, usuario, foto["vivero_id"], "revisar foto", f"{foto_id} {valor}")
    return volver(f"/viveros/{foto['vivero_id']}/camaras/{foto['camara_id']}")


def imagen(peticion, foto_id, categoria):
    config = cargar_configuracion()
    with conectar(config) as conexion:
        exigir(conexion, peticion)
        registro = fila(conexion, "SELECT * FROM capturas WHERE id = %s", (foto_id,), "Foto desconocida.")
        exigir(conexion, peticion, registro["vivero_id"])
    return FileResponse(foto_ruta(config, categoria, registro["clave_archivo"]), media_type="image/jpeg")


@app.get("/fotos/{foto_id}.jpg", name="foto")
def foto(peticion: Request, foto_id: int):
    return imagen(peticion, foto_id, "originales")


@app.get("/miniaturas/{foto_id}.jpg", name="miniatura")
def miniatura(peticion: Request, foto_id: int):
    return imagen(peticion, foto_id, "miniaturas")


@app.get("/trabajos")
def trabajos(peticion: Request):
    with conectar(cargar_configuracion()) as conexion:
        usuario = exigir(conexion, peticion)
        sitios = viveros_visibles(conexion, usuario)
        registros = conexion.execute(
            """SELECT t.*, v.nombre, (%s OR t.usuario_id = %s OR p.rol = 'tecnico') AS puede_reintentar
            FROM trabajos t JOIN viveros v ON v.id = t.vivero_id
            LEFT JOIN permisos p ON p.usuario_id = %s AND p.vivero_id = t.vivero_id
            WHERE t.vivero_id = ANY(%s) AND (t.tipo <> 'analisis' OR t.estado = 'error')
            ORDER BY t.id DESC LIMIT 100""", (usuario["administrador"], usuario["id"], usuario["id"],
                                              [v["id"] for v in sitios]),
        ).fetchall()
        resumen = conexion.execute(
            """SELECT estado, count(*) AS cantidad FROM trabajos WHERE vivero_id = ANY(%s) AND estado <> 'listo'
            GROUP BY estado""", ([v["id"] for v in sitios],),
        ).fetchall()
    return pagina(peticion, "trabajos.html", usuario, trabajos=registros, resumen=resumen, titulo="Procesamiento")


@app.get("/trabajos/{trabajo}/archivo")
def archivo_trabajo(peticion: Request, trabajo: int):
    config = cargar_configuracion()
    with conectar(config) as conexion:
        exigir(conexion, peticion)
        registro = fila(conexion, "SELECT * FROM trabajos WHERE id = %s", (trabajo,), "Trabajo desconocido.")
        exigir(conexion, peticion, registro["vivero_id"])
    if registro["estado"] != "listo" or not registro["salida"]:
        raise HTTPException(404, "Archivo pendiente.")
    ruta = foto_ruta(config, "trabajos", registro["salida"])
    if registro["tipo"] == "timelapse":
        return FileResponse(ruta, media_type="video/mp4", filename=ruta.name, content_disposition_type="inline")
    return FileResponse(ruta, filename=ruta.name)


@app.post("/trabajos/{trabajo}/reintentar")
def reintentar(peticion: Request, trabajo: int, datos: FormData = Depends(datos_formulario)):
    with conectar(cargar_configuracion()) as conexion:
        registro = fila(conexion, "SELECT * FROM trabajos WHERE id = %s", (trabajo,), "Trabajo desconocido.")
        usuario = exigir(conexion, peticion, registro["vivero_id"], formulario=datos)
        if usuario.get("rol") != "tecnico" and usuario["id"] != registro["usuario_id"]:
            raise HTTPException(403, "Tu cuenta no puede reintentar este trabajo.")
        conexion.execute(
            """UPDATE trabajos SET estado = 'pendiente', intentos = 0, disponible_en = now(), mensaje = ''
            WHERE id = %s AND estado = 'error'""", (trabajo,),
        )
    return volver("/trabajos")


@app.get("/alertas")
def alertas(peticion: Request, cerradas: bool = False):
    with conectar(cargar_configuracion()) as conexion:
        usuario = exigir(conexion, peticion)
        ids = [v["id"] for v in viveros_visibles(conexion, usuario)]
        registros = conexion.execute(
            """SELECT a.*, v.nombre, u.nombre AS revisora, (%s OR p.rol = 'tecnico') AS puede_marcar FROM alertas a
            LEFT JOIN viveros v ON v.id = a.vivero_id LEFT JOIN usuarios u ON u.id = a.leida_por
            LEFT JOIN permisos p ON p.usuario_id = %s AND p.vivero_id = a.vivero_id
            WHERE (a.vivero_id = ANY(%s) OR (a.vivero_id IS NULL AND %s))
            AND (%s OR a.cerrada_en IS NULL) ORDER BY a.id DESC LIMIT 200""",
            (usuario["administrador"], usuario["id"], ids, usuario["administrador"], cerradas),
        ).fetchall()
    return pagina(peticion, "alertas.html", usuario, alertas=registros, cerradas=cerradas, titulo="Alertas")


@app.post("/alertas/{alerta}/leer")
def leer_alerta(peticion: Request, alerta: int, datos: FormData = Depends(datos_formulario)):
    with conectar(cargar_configuracion()) as conexion:
        registro = fila(conexion, "SELECT * FROM alertas WHERE id = %s", (alerta,), "Alerta desconocida.")
        usuario = exigir(conexion, peticion, registro["vivero_id"], editar=True,
                         admin=registro["vivero_id"] is None, formulario=datos)
        conexion.execute("UPDATE alertas SET leida_por = %s WHERE id = %s", (usuario["id"], alerta))
    return volver("/alertas")


@app.get("/supervisor")
def supervisor(peticion: Request, parcial: bool = False):
    config = cargar_configuracion()
    with conectar(config) as conexion:
        usuario = exigir(conexion, peticion)
        estado = estado_supervisor(conexion, config, usuario)
    plantilla = "supervisor_estado.html" if parcial else "supervisor.html"
    return pagina(peticion, plantilla, usuario, estado=estado, titulo="Supervisor")


@app.get("/diagnostico")
def diagnostico(peticion: Request):
    config = cargar_configuracion()
    with conectar(config) as conexion:
        usuario = exigir(conexion, peticion)
        viveros = resumen_camaras(conexion, viveros_visibles(conexion, usuario))
        ids = [v["id"] for v in viveros]
        rechazos = conexion.execute(
            """SELECT vivero_id, nodo_id, camara_id, ruta, problema FROM origenes
            WHERE problema IS NOT NULL AND vivero_id = ANY(%s) ORDER BY mtime_ns DESC LIMIT 60""", (ids,),
        ).fetchall()
        importador = conexion.execute("SELECT * FROM estado_importador WHERE id").fetchone()
        disco = shutil.disk_usage(config.archivo) if usuario["administrador"] else None
    return pagina(peticion, "diagnostico.html", usuario, sitios=viveros, importador=importador,
                  rechazos=rechazos, disco=disco, titulo="Diagnóstico")
