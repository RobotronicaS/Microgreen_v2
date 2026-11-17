"""Configuración de avisos y respaldos con trabajos persistentes."""

from datetime import datetime
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
from tempfile import NamedTemporaryFile

from fastapi import APIRouter, Depends, Request
from psycopg.types.json import Jsonb
from starlette.datastructures import FormData

from .acceso import exigir
from .avisos import enviar_correo, enviar_telegram
from .base import auditar, conectar
from .configuracion import cargar_configuracion, ruta_configuracion
from .dominio import texto
from .ingesta import sincronizar
from .respaldo import respaldar, ultimo_respaldo, verificar_respaldo
from .web import datos_formulario, fila, pagina, volver

rutas = APIRouter()
TIPOS = {"respaldo": "Crear respaldo", "verificar": "Verificar respaldo", "correo": "Probar correo", "telegram": "Probar Telegram"}
ID = re.compile(r"[a-f0-9]{32}")


def leer_opciones(nombre):
    ruta = ruta_configuracion().with_name(nombre + ".json")
    if not ruta.exists():
        return {}
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    if not isinstance(datos, dict):
        raise ValueError(f"Revisá la configuración de {nombre}.")
    return datos


def guardar_opciones(nombre, datos):
    ruta = ruta_configuracion().with_name(nombre + ".json")
    temporal = None
    try:
        with NamedTemporaryFile(dir=ruta.parent, prefix=".opciones-", delete=False) as salida:
            temporal = Path(salida.name)
            salida.write((json.dumps(datos, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
            salida.flush()
            os.fsync(salida.fileno())
        temporal.replace(ruta)
        sincronizar(ruta.parent)
    finally:
        if temporal:
            temporal.unlink(missing_ok=True)


def validar_canal(canal, opciones):
    if canal == "correo":
        if (not opciones.get("servidor") or not opciones.get("usuario") or not opciones.get("clave")
                or opciones.get("modo") not in ("ssl", "starttls") or not 1 <= opciones.get("puerto", 0) <= 65535):
            raise ValueError("Completá el servidor, puerto, modo, usuario y contraseña del correo.")
        direcciones = [opciones.get("desde", ""), *opciones.get("para", [])]
        if len(direcciones) < 2 or len(direcciones) > 21 or any(
            not re.fullmatch(r"[^@\s<>]+@[^@\s<>]+", direccion) for direccion in direcciones
        ):
            raise ValueError("Revisá el remitente y las direcciones de destino.")
    elif (not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", opciones.get("token", ""))
          or not opciones.get("chat_id")):
        raise ValueError("Completá el token del bot y el identificador del chat de Telegram.")


@rutas.get("/mantenimiento")
def ajustes(peticion: Request, operacion: str = "", guardado: str = ""):
    config = cargar_configuracion()
    with conectar(config) as conexion:
        usuario = exigir(conexion, peticion, admin=True)
        trabajos = conexion.execute("SELECT * FROM mantenimiento ORDER BY creada_en DESC LIMIT 12").fetchall()
        errores_avisos = conexion.execute(
            "SELECT count(*) AS n FROM avisos WHERE enviada_en IS NULL AND intentos >= 5",
        ).fetchone()["n"]
    avisos = leer_opciones("avisos")
    correo = {**avisos.get("correo", {})}
    telegram = {**avisos.get("telegram", {})}
    correo["clave_guardada"] = bool(correo.pop("clave", ""))
    telegram["token_guardado"] = bool(telegram.pop("token", ""))
    respaldo = leer_opciones("respaldo")
    respaldo["habilitado"] = respaldo.get("habilitado", bool(respaldo.get("destino")))
    ultima = None
    mismo_disco = False
    if respaldo.get("destino"):
        base = Path(respaldo["destino"]).expanduser()
        ultima = ultimo_respaldo(base)
        if base.is_dir():
            mismo_disco = base.stat().st_dev == config.archivo.stat().st_dev
    seleccionado = next((t for t in trabajos if t["id"] == operacion), None)
    if not seleccionado:
        seleccionado = next((t for t in trabajos if t["estado"] in ("pendiente", "en_curso")), None)
    return pagina(peticion, "mantenimiento.html", usuario, correo=correo, telegram=telegram,
                  respaldo=respaldo, ultimo=ultima, mismo_disco=mismo_disco, trabajos=trabajos,
                  seleccionado=seleccionado, tipos=TIPOS, nonce=secrets.token_hex(16),
                  errores_avisos=errores_avisos, guardado=guardado, titulo="Avisos y respaldos")


@rutas.post("/mantenimiento/avisos")
def guardar_avisos(peticion: Request, datos: FormData = Depends(datos_formulario)):
    with conectar(cargar_configuracion()) as conexion:
        usuario = exigir(conexion, peticion, admin=True, formulario=datos)
        with conexion.transaction():
            conexion.execute("SELECT pg_advisory_xact_lock(431007101)")
            anteriores = leer_opciones("avisos")
            try:
                puerto = int(datos.get("correo_puerto") or 587)
            except ValueError:
                raise ValueError("Ingresá un puerto SMTP entre 1 y 65535.") from None
            if not 1 <= puerto <= 65535:
                raise ValueError("Ingresá un puerto SMTP entre 1 y 65535.")
            correo = {
                "habilitado": datos.get("correo_habilitado") == "si",
                "servidor": texto(datos.get("correo_servidor", ""), 253),
                "puerto": puerto, "modo": datos.get("correo_modo", "starttls"),
                "usuario": texto(datos.get("correo_usuario", ""), 256),
                "clave": datos.get("correo_clave", "") or anteriores.get("correo", {}).get("clave", ""),
                "desde": texto(datos.get("correo_desde", ""), 254),
                "para": [direccion.strip() for direccion in datos.get("correo_para", "").split(",") if direccion.strip()],
            }
            telegram = {
                "habilitado": datos.get("telegram_habilitado") == "si",
                "token": datos.get("telegram_token", "") or anteriores.get("telegram", {}).get("token", ""),
                "chat_id": texto(datos.get("telegram_chat", ""), 128),
            }
            for canal, opciones in (("correo", correo), ("telegram", telegram)):
                if opciones["habilitado"]:
                    validar_canal(canal, opciones)
            if len(correo["clave"]) > 512 or len(telegram["token"]) > 512:
                raise ValueError("La credencial supera el tamaño permitido.")
            guardar_opciones("avisos", {**anteriores, "correo": correo, "telegram": telegram})
            for canal, opciones in (("correo", correo), ("telegram", telegram)):
                if not opciones["habilitado"]:
                    continue
                conexion.execute(
                    """INSERT INTO avisos (alerta_id, canal, recuperada)
                    SELECT id, %s, false FROM alertas WHERE cerrada_en IS NULL ON CONFLICT DO NOTHING""", (canal,),
                )
                if opciones != anteriores.get(canal):
                    conexion.execute(
                        """UPDATE avisos SET intentos = 0, siguiente = now()
                        WHERE canal = %s AND enviada_en IS NULL AND intentos > 0""", (canal,),
                    )
            auditar(conexion, usuario, None, "configurar avisos")
    return volver("/mantenimiento?guardado=avisos#avisos")


@rutas.post("/mantenimiento/respaldo")
def guardar_respaldo(peticion: Request, datos: FormData = Depends(datos_formulario)):
    config = cargar_configuracion()
    with conectar(config) as conexion:
        usuario = exigir(conexion, peticion, admin=True, formulario=datos)
        destino = texto(datos.get("destino", ""), 1024)
        habilitado = datos.get("habilitado") == "si"
        if habilitado and not destino:
            raise ValueError("Ingresá la carpeta de destino del respaldo diario.")
        if destino:
            ruta = Path(destino).expanduser()
            if not ruta.is_absolute():
                raise ValueError("Ingresá una ruta absoluta para los respaldos.")
            ruta = ruta.resolve()
            if ruta.is_relative_to(config.archivo) or config.archivo.is_relative_to(ruta):
                raise ValueError("Elegí una carpeta separada del archivo de capturas.")
            destino = str(ruta)
        with conexion.transaction():
            conexion.execute("SELECT pg_advisory_xact_lock(431007101)")
            guardar_opciones("respaldo", {**leer_opciones("respaldo"), "destino": destino, "habilitado": habilitado})
            auditar(conexion, usuario, None, "configurar respaldo")
    return volver("/mantenimiento?guardado=respaldo#respaldos")


def encolar(conexion, tipo, solicitante=None, clave=None):
    if tipo not in TIPOS:
        raise ValueError("Operación de mantenimiento desconocida.")
    with conexion.transaction():
        conexion.execute("SELECT pg_advisory_xact_lock(431007102)")
        anterior = conexion.execute("SELECT id FROM mantenimiento WHERE clave = %s", (clave,)).fetchone() if clave else None
        if anterior:
            return anterior["id"]
        abierto = conexion.execute(
            "SELECT id FROM mantenimiento WHERE tipo = %s AND estado IN ('pendiente', 'en_curso')", (tipo,),
        ).fetchone()
        if abierto:
            return abierto["id"]
        trabajo = secrets.token_hex(16)
        conexion.execute("INSERT INTO mantenimiento (id, tipo, solicitante, clave) VALUES (%s, %s, %s, %s)",
                         (trabajo, tipo, solicitante, clave))
    return trabajo


@rutas.post("/mantenimiento/operaciones")
def solicitar(peticion: Request, datos: FormData = Depends(datos_formulario)):
    with conectar(cargar_configuracion()) as conexion:
        usuario = exigir(conexion, peticion, admin=True, formulario=datos)
        tipo = datos.get("tipo", "")
        nonce = datos.get("nonce", "")
        if not ID.fullmatch(nonce):
            raise ValueError("Actualizá la página y volvé a intentar.")
        if tipo in ("correo", "telegram"):
            validar_canal(tipo, leer_opciones("avisos").get(tipo, {}))
        elif tipo in ("respaldo", "verificar") and not leer_opciones("respaldo").get("destino"):
            raise ValueError("Guardá primero la carpeta de destino del respaldo.")
        trabajo = encolar(conexion, tipo, usuario["id"], tipo + ":" + nonce)
        auditar(conexion, usuario, None, "solicitar mantenimiento", tipo)
    return volver("/mantenimiento?operacion=" + trabajo + "#operaciones")


@rutas.get("/api/mantenimiento/{trabajo}")
def estado(peticion: Request, trabajo: str):
    with conectar(cargar_configuracion()) as conexion:
        exigir(conexion, peticion, admin=True)
        return fila(conexion, "SELECT estado, mensaje, resultado FROM mantenimiento WHERE id = %s",
                    (trabajo,), "Operación desconocida.")


def ejecutar_operacion(config, tipo):
    if tipo in ("correo", "telegram"):
        opciones = leer_opciones("avisos").get(tipo, {})
        validar_canal(tipo, opciones)
        enviar = enviar_correo if tipo == "correo" else enviar_telegram
        enviar(opciones, "MicroGreenVision · prueba de aviso", "La configuración de avisos permite enviar este mensaje de prueba.")
        return {"mensaje": "Mensaje de prueba enviado."}
    opciones = leer_opciones("respaldo")
    if not opciones.get("destino"):
        raise ValueError("Configurá la carpeta de destino del respaldo.")
    base = Path(opciones["destino"]).expanduser().resolve()
    if tipo == "respaldo":
        base.mkdir(parents=True, exist_ok=True, mode=0o700)
        ruta = respaldar(config, base)
        return {"respaldo": str(ruta), "mensaje": "Respaldo terminado. Podés verificar su integridad desde este panel."}
    ruta = ultimo_respaldo(base)
    if ruta is None:
        raise ValueError("Primero generá un respaldo completo.")
    return {**verificar_respaldo(ruta), "respaldo": str(ruta)}


def procesar_mantenimiento(config):
    with conectar(config) as conexion:
        if not conexion.execute("SELECT pg_try_advisory_lock(431007103) AS adquirido").fetchone()["adquirido"]:
            return
        conexion.execute("""UPDATE mantenimiento SET estado = 'error', mensaje = 'El proceso se interrumpió. Podés volver a solicitarlo.',
                         terminada_en = now() WHERE estado = 'en_curso'""")
        trabajo = conexion.execute(
            """UPDATE mantenimiento SET estado = 'en_curso', inicio_en = now(), mensaje = 'Procesando la solicitud.'
            WHERE id = (SELECT id FROM mantenimiento WHERE estado = 'pendiente' ORDER BY creada_en LIMIT 1)
            RETURNING *""",
        ).fetchone()
        if trabajo is None:
            return
        try:
            resultado = ejecutar_operacion(config, trabajo["tipo"])
            estado, mensaje = "ok", resultado["mensaje"]
        except ValueError as error:
            estado, mensaje, resultado = "error", str(error), {}
        except (OSError, KeyError, TypeError, subprocess.SubprocessError):
            estado = "error"
            mensaje = "La operación falló. Revisá la configuración, la conexión o el destino y volvé a solicitarla."
            resultado = {}
        conexion.execute("UPDATE mantenimiento SET estado = %s, mensaje = %s, resultado = %s, terminada_en = now() WHERE id = %s",
                         (estado, mensaje, Jsonb(resultado), trabajo["id"]))


def programar_respaldo(config):
    opciones = leer_opciones("respaldo")
    if not opciones.get("habilitado", bool(opciones.get("destino"))):
        return
    if not opciones.get("destino"):
        raise ValueError("Falta configurar el destino del respaldo diario.")
    dia = datetime.now(config.zona).date().isoformat()
    with conectar(config) as conexion:
        encolar(conexion, "respaldo", clave="diario:" + dia)
