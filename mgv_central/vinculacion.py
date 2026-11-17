"""Vinculación de equipos Zero desde el panel de Central."""

from ipaddress import ip_address
import re
import secrets
from threading import Thread
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
import paramiko
from starlette.datastructures import FormData

from .acceso import exigir, huella
from .base import auditar, conectar
from .configuracion import cargar_configuracion
from .ssh_zero import instalar
from .web import datos_formulario, fila, pagina, volver

rutas = APIRouter()
NOMBRE_HOST = re.compile(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
USUARIO_SSH = re.compile(r"[a-z_][a-z0-9_.-]{0,31}")
ABIERTAS = "estado IN ('pendiente', 'en_curso')"


def datos_ssh(datos):
    host = datos.get("host", "").strip().lower()
    try:
        direccion = ip_address(host)
    except ValueError:
        if not NOMBRE_HOST.fullmatch(host) or host == "localhost":
            raise ValueError("Ingresá la IP o el nombre de Tailscale de la Zero.") from None
    else:
        if direccion.is_loopback or direccion.is_unspecified or direccion.is_multicast:
            raise ValueError("Ingresá la dirección de la Raspberry Pi Zero.")
    usuario = datos.get("usuario_ssh", "").strip()
    if not USUARIO_SSH.fullmatch(usuario) or usuario == "root":
        raise ValueError("Ingresá el usuario habitual de la Raspberry con permiso sudo.")
    clave = datos.get("clave_ssh", "")
    if not 1 <= len(clave) <= 256 or any(c in clave for c in ("\x00", "\n", "\r")):
        raise ValueError("Ingresá la contraseña de ese usuario en la Raspberry.")
    try:
        puerto = int(datos.get("puerto") or 22)
    except ValueError:
        raise ValueError("El puerto SSH debe ser un número entre 1 y 65535.") from None
    if not 1 <= puerto <= 65535:
        raise ValueError("El puerto SSH debe ser un número entre 1 y 65535.")
    url = datos.get("url", "").strip().rstrip("/")
    try:
        partes = urlsplit(url)
        puerto_central = partes.port
    except ValueError:
        raise ValueError("Revisá la dirección HTTPS de Central.") from None
    if (partes.scheme != "https" or not partes.hostname or partes.username or partes.password
            or partes.query or partes.fragment or partes.path not in ("", "/") or puerto_central == 0):
        raise ValueError("Ingresá el enlace HTTPS privado de Central.")
    return {"host": host, "puerto": puerto, "usuario_ssh": usuario, "url": url}, clave


def vencer(conexion):
    conexion.execute(
        """UPDATE vinculaciones_zero SET estado = 'error', actualizada_en = now(), terminada_en = now(),
           mensaje = 'La instalación se interrumpió. Volvé a vincular la Zero desde su ficha.'
           WHERE estado IN ('pendiente', 'en_curso')
           AND (actualizada_en < now() - interval '2 minutes' OR creada_en < now() - interval '20 minutes')""",
    )


@rutas.get("/viveros/{vivero}/nodos/{nodo}/conectar")
def formulario(peticion: Request, vivero: str, nodo: str):
    with conectar(cargar_configuracion()) as conexion:
        usuario = exigir(conexion, peticion, vivero, admin=True)
        equipo = fila(conexion, "SELECT nombre FROM nodos WHERE vivero_id = %s AND id = %s",
                      (vivero, nodo), "Zero desconocida.")
        vencer(conexion)
        abierta = conexion.execute(
            "SELECT id FROM vinculaciones_zero WHERE vivero_id = %s AND nodo_id = %s AND " + ABIERTAS,
            (vivero, nodo),
        ).fetchone()
    if abierta:
        return volver("/vinculaciones-zero/" + abierta["id"])
    return pagina(peticion, "vincular_zero.html", usuario, nombre=equipo["nombre"], vivero=vivero, nodo=nodo,
                  url=str(peticion.base_url).rstrip("/"), titulo="Vincular Zero")


@rutas.post("/viveros/{vivero}/nodos/{nodo}/conectar")
def iniciar(peticion: Request, vivero: str, nodo: str, datos: FormData = Depends(datos_formulario)):
    config = cargar_configuracion()
    with conectar(config) as conexion:
        usuario = exigir(conexion, peticion, vivero, admin=True, formulario=datos)
        acceso, clave = datos_ssh(datos)
        with conexion.transaction():
            conexion.execute("SELECT pg_advisory_xact_lock(431006001)")
            vencer(conexion)
            equipo = fila(conexion, "SELECT nombre, token_sha FROM nodos WHERE vivero_id = %s AND id = %s FOR UPDATE",
                          (vivero, nodo), "Zero desconocida.")
            abierta = conexion.execute(
                "SELECT id FROM vinculaciones_zero WHERE vivero_id = %s AND nodo_id = %s AND " + ABIERTAS,
                (vivero, nodo),
            ).fetchone()
            if abierta:
                return volver("/vinculaciones-zero/" + abierta["id"])
            if conexion.execute(
                "SELECT 1 FROM vinculaciones_zero WHERE host = %s AND puerto = %s AND " + ABIERTAS,
                (acceso["host"], acceso["puerto"]),
            ).fetchone():
                raise HTTPException(409, "Esa Raspberry ya tiene una instalación en curso.")
            cantidad = conexion.execute("SELECT count(*) AS n FROM vinculaciones_zero WHERE " + ABIERTAS).fetchone()["n"]
            if cantidad >= 2:
                raise HTTPException(429, "Esperá a que termine alguna de las dos instalaciones en curso.")
            trabajo = secrets.token_hex(16)
            registro = conexion.execute(
                """INSERT INTO vinculaciones_zero
                (id, vivero_id, nodo_id, solicitante, host, puerto, usuario_ssh, token_previo_sha)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
                (trabajo, vivero, nodo, usuario["id"], acceso["host"], acceso["puerto"], acceso["usuario_ssh"], equipo["token_sha"]),
            ).fetchone()
            auditar(conexion, usuario, vivero, "iniciar vinculación Zero", nodo)
    try:
        Thread(target=vincular, args=(config, registro, acceso["url"], clave), daemon=True).start()
    except RuntimeError:
        with conectar(config) as conexion:
            conexion.execute("UPDATE vinculaciones_zero SET estado = 'error', mensaje = %s, terminada_en = now() WHERE id = %s",
                             ("Central no pudo iniciar la instalación. Volvé a intentar.", trabajo))
    return volver("/vinculaciones-zero/" + trabajo)


def vincular(config, equipo, url, clave):
    token = secrets.token_urlsafe(32)

    def latido(mensaje=None):
        with conectar(config) as conexion:
            actualizado = conexion.execute(
                """UPDATE vinculaciones_zero SET actualizada_en = now(), mensaje = COALESCE(%s, mensaje)
                WHERE id = %s AND estado = 'en_curso' RETURNING id""", (mensaje, equipo["id"]),
            ).fetchone()
        if not actualizado:
            raise ValueError("La instalación se interrumpió. Volvé a intentar desde la ficha de la Zero.")

    def activar():
        with conectar(config) as conexion:
            with conexion.transaction():
                trabajo = conexion.execute("SELECT estado FROM vinculaciones_zero WHERE id = %s FOR UPDATE", (equipo["id"],)).fetchone()
                if trabajo["estado"] != "en_curso":
                    raise ValueError("La instalación se interrumpió. Volvé a vincular la Zero.")
                cambio = conexion.execute(
                    """UPDATE nodos SET token_sha = %s, autorizado_en = now()
                    WHERE vivero_id = %s AND id = %s AND token_sha IS NOT DISTINCT FROM %s RETURNING id""",
                    (huella(token), equipo["vivero_id"], equipo["nodo_id"], equipo["token_previo_sha"]),
                ).fetchone()
                if not cambio:
                    raise ValueError("La credencial de la Zero cambió durante la instalación. Volvé a vincularla.")
                conexion.execute(
                    """UPDATE vinculaciones_zero SET estado = 'ok', mensaje = 'Zero vinculada y lista para agregar cámaras.',
                    actualizada_en = now(), terminada_en = now() WHERE id = %s""", (equipo["id"],),
                )

    try:
        with conectar(config) as conexion:
            conexion.execute(
                """UPDATE vinculaciones_zero SET estado = 'en_curso', token_sha = %s,
                mensaje = 'Conectando con la Raspberry. La preparación puede tardar varios minutos.', actualizada_en = now()
                WHERE id = %s AND estado = 'pendiente'""", (huella(token), equipo["id"]),
            )
        credencial = {"url": url, "vivero": equipo["vivero_id"], "nodo": equipo["nodo_id"], "token": token}
        instalar(equipo, clave, credencial, latido, activar)
        return
    except paramiko.AuthenticationException:
        mensaje = "La Zero rechazó el usuario o la contraseña. Revisalos y volvé a intentar."
    except paramiko.BadHostKeyException:
        mensaje = "La identidad SSH de esa dirección cambió. Verificá qué Raspberry tiene esa IP."
    except (TimeoutError, paramiko.ssh_exception.NoValidConnectionsError):
        mensaje = "Central no pudo conectarse a la Zero. Revisá su dirección, Tailscale y que SSH esté habilitado."
    except Exception as error:
        mensaje = str(error) if isinstance(error, ValueError) else "La instalación se interrumpió. Revisá la conexión de la Zero y volvé a intentar."
    mensaje = mensaje.replace(clave, "[contraseña]").replace(token, "[credencial]")[:2400]
    with conectar(config) as conexion:
        conexion.execute(
            """UPDATE vinculaciones_zero SET estado = 'error', mensaje = %s, actualizada_en = now(), terminada_en = now()
            WHERE id = %s AND estado IN ('pendiente', 'en_curso')""", (mensaje, equipo["id"]),
        )


@rutas.get("/vinculaciones-zero/{trabajo}")
def progreso(peticion: Request, trabajo: str):
    with conectar(cargar_configuracion()) as conexion:
        usuario = exigir(conexion, peticion, admin=True)
        vencer(conexion)
        registro = fila(conexion, "SELECT * FROM vinculaciones_zero WHERE id = %s", (trabajo,), "Vinculación desconocida.")
    return pagina(peticion, "vinculacion_estado.html", usuario, trabajo=registro, titulo="Vincular Zero")


@rutas.get("/api/vinculaciones-zero/{trabajo}")
def estado(peticion: Request, trabajo: str):
    with conectar(cargar_configuracion()) as conexion:
        exigir(conexion, peticion, admin=True)
        vencer(conexion)
        registro = fila(conexion, "SELECT estado, mensaje FROM vinculaciones_zero WHERE id = %s",
                        (trabajo,), "Vinculación desconocida.")
    return registro
