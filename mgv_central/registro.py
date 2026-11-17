"""Registro de visitantes con autorización de consulta por invernáculo."""

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.datastructures import FormData

from .acceso import codificar_clave, csrf_valido, exigir, huella, usuario_actual, validar_cuenta
from .base import auditar, conectar
from .configuracion import cargar_configuracion
from .dominio import identificador, texto
from .web import datos_formulario, fila, pagina, volver

rutas = APIRouter()
COOKIE_REGISTRO = "mgv_registro"


def formulario_registro(peticion, config, error="", codigo=200, persona="", nombre=""):
    csrf = secrets.token_urlsafe(32)
    respuesta = pagina(peticion, "registrarse.html", codigo=codigo, csrf=csrf, error=error,
                       persona=persona, nombre=nombre, titulo="Registrarse")
    respuesta.set_cookie(COOKIE_REGISTRO, csrf, max_age=600, httponly=True, samesite="strict",
                         secure=config.cookies_seguras or peticion.url.scheme == "https")
    return respuesta


@rutas.get("/registrarse")
def registrarse(peticion: Request):
    config = cargar_configuracion()
    with conectar(config) as conexion:
        if usuario_actual(conexion, peticion):
            return volver("/")
    return formulario_registro(peticion, config)


def limitar_registro(conexion, ip):
    conexion.execute("DELETE FROM intentos_login WHERE desde < now() - interval '1 day'")
    contador = conexion.execute(
        """INSERT INTO intentos_login (clave, intentos) VALUES (%s, 1)
        ON CONFLICT (clave) DO UPDATE SET
            intentos = CASE WHEN intentos_login.desde < now() - interval '15 minutes'
                THEN 1 ELSE intentos_login.intentos + 1 END,
            desde = CASE WHEN intentos_login.desde < now() - interval '15 minutes'
                THEN now() ELSE intentos_login.desde END RETURNING intentos""",
        (huella("registro:ip:" + ip),),
    ).fetchone()["intentos"]
    if contador > 60:
        raise HTTPException(429, "Se recibieron muchos registros. Volvé a intentar en 15 minutos.")


@rutas.post("/registrarse")
def solicitar_registro(peticion: Request, datos: FormData = Depends(datos_formulario)):
    config = cargar_configuracion()
    if not csrf_valido(datos.get("csrf"), peticion.cookies.get(COOKIE_REGISTRO)):
        return formulario_registro(peticion, config, "Volvé a completar el formulario para registrarte.", 403)
    persona = nombre = ""
    try:
        persona = texto(datos.get("persona", ""), 80, True)
        if any(ord(letra) < 32 for letra in persona):
            raise ValueError("Ingresá tu nombre y apellido en una sola línea.")
        nombre = validar_cuenta(datos.get("usuario", "").strip().lower())
        clave = datos.get("clave", "")
        if clave != datos.get("confirmar", ""):
            raise ValueError("Las contraseñas deben coincidir.")
        # La validación y el límite se aplican antes del cálculo de la contraseña.
        if not 12 <= len(clave) <= 128:
            raise ValueError("La contraseña debe tener entre 12 y 128 caracteres.")
        with conectar(config) as conexion:
            limitar_registro(conexion, peticion.client.host if peticion.client else "local")
            with conexion.transaction():
                conexion.execute("SELECT pg_advisory_xact_lock(431008101)")
                if conexion.execute("SELECT 1 FROM usuarios WHERE nombre = %s", (nombre,)).fetchone():
                    raise ValueError("Ese usuario no está disponible. Elegí otro o ingresá con tu cuenta.")
                if conexion.execute(
                    "SELECT 1 FROM solicitudes_acceso WHERE nombre = %s AND estado = 'pendiente'", (nombre,),
                ).fetchone():
                    raise ValueError("Ese usuario ya tiene un registro pendiente. Esperá la autorización del administrador.")
                pendientes = conexion.execute(
                    "SELECT count(*) AS n FROM solicitudes_acceso WHERE estado = 'pendiente'",
                ).fetchone()["n"]
                if pendientes >= 200:
                    raise HTTPException(503, "El administrador debe revisar los registros pendientes antes de recibir más solicitudes.")
                conexion.execute(
                    "INSERT INTO solicitudes_acceso (nombre, persona, clave) VALUES (%s, %s, %s)",
                    (nombre, persona, codificar_clave(clave)),
                )
    except ValueError as error:
        return formulario_registro(peticion, config, str(error), 400, persona=persona, nombre=nombre)
    respuesta = volver("/registro-recibido")
    respuesta.delete_cookie(COOKIE_REGISTRO)
    return respuesta


@rutas.get("/registro-recibido")
def registro_recibido(peticion: Request):
    return pagina(peticion, "registro_recibido.html", titulo="Registro recibido")


@rutas.post("/gestion/solicitudes/{solicitud_id}")
def resolver_solicitud(peticion: Request, solicitud_id: int, datos: FormData = Depends(datos_formulario)):
    with conectar(cargar_configuracion()) as conexion:
        administrador = exigir(conexion, peticion, admin=True, formulario=datos)
        accion = datos.get("accion", "")
        if accion not in ("aprobar", "rechazar"):
            raise ValueError("Elegí autorizar o rechazar el registro.")
        with conexion.transaction():
            solicitud = fila(conexion, "SELECT * FROM solicitudes_acceso WHERE id = %s FOR UPDATE",
                             (solicitud_id,), "Registro desconocido.")
            if solicitud["estado"] != "pendiente":
                return volver("/gestion#solicitudes")
            cuenta = None
            if accion == "aprobar":
                sitios = sorted({identificador(v) for v in datos.getlist("viveros")})
                if not sitios or len(sitios) > 100:
                    raise ValueError("Elegí al menos un invernáculo que esta cuenta pueda visualizar.")
                existentes = conexion.execute("SELECT id FROM viveros WHERE id = ANY(%s)", (sitios,)).fetchall()
                if len(existentes) != len(sitios):
                    raise ValueError("Actualizá la página y elegí invernáculos existentes.")
                cuenta = conexion.execute(
                    """INSERT INTO usuarios (nombre, clave, administrador, activo) VALUES (%s, %s, false, true)
                    ON CONFLICT (nombre) DO NOTHING RETURNING id""", (solicitud["nombre"], solicitud["clave"]),
                ).fetchone()
                if cuenta is None:
                    raise HTTPException(409, "Ya existe una cuenta con ese usuario. Revisá el registro antes de autorizarlo.")
                for vivero in sitios:
                    conexion.execute(
                        "INSERT INTO permisos (usuario_id, vivero_id, rol) VALUES (%s, %s, 'consulta')",
                        (cuenta["id"], vivero),
                    )
            conexion.execute(
                """UPDATE solicitudes_acceso SET estado = %s, clave = NULL, resuelta_en = now(),
                resuelta_por = %s, cuenta_id = %s WHERE id = %s""",
                ("aprobada" if cuenta else "rechazada", administrador["id"], cuenta["id"] if cuenta else None, solicitud_id),
            )
            auditar(conexion, administrador, None, "autorizar consulta" if cuenta else "rechazar registro", solicitud["nombre"])
    return volver("/gestion#solicitudes")
