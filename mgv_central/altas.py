"""Alta de cámaras desde Central con prueba en el nodo de captura."""

from datetime import UTC, datetime, timedelta
from ipaddress import IPv4Address
import json
import re
import secrets
from time import time
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg.types.json import Jsonb
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import FormData

from .acceso import exigir
from .base import auditar, conectar
from .configuracion import cargar_configuracion, ruta_configuracion
from .dominio import PUESTOS, identificador, texto
from .gestion import registrar_origenes
from .ingesta import guardar_archivo
from .receptor import autenticar, json_limitado, nombre_jpg
from .resumen import alta_disponible, captura_disponible
from .web import datos_formulario, fila, pagina, sitio

rutas = APIRouter()
ID_ALTA = re.compile(r"[a-f0-9]{32}")
ABIERTA = ("pendiente", "en_curso")
HILERAS = dict(PUESTOS)
VENCIDA = "La Zero no terminó la prueba a tiempo. Revisá el enlace y volvé a intentar."


def archivo_perfil(alta):
    return ruta_configuracion().with_name(".altas-camara") / f"{alta}.json"


def retirar_perfil(alta):
    try:
        archivo_perfil(alta).unlink(missing_ok=True)
    except OSError:
        print("No se pudo retirar un perfil temporal de cámara.", flush=True)


def vencer(conexion):
    vencidas = conexion.execute(
        """UPDATE altas_camara SET estado = 'error', resultado = %s, terminada_en = now()
        WHERE estado IN ('pendiente', 'en_curso') AND vence_en <= now() RETURNING id""",
        (Jsonb({"mensaje": VENCIDA}),),
    ).fetchall()
    for alta in vencidas:
        retirar_perfil(alta["id"])


def limpiar_altas(conexion):
    vencer(conexion)
    limite = time() - 600
    for ruta in archivo_perfil("temporal").parent.glob("*.json"):
        if not ID_ALTA.fullmatch(ruta.stem):
            continue
        try:
            if ruta.stat().st_mtime < limite:
                retirar_perfil(ruta.stem)
        except FileNotFoundError:
            continue


def ipv4_camara(valor):
    if not isinstance(valor, str):
        raise ValueError("Ingres\u00e1 la IPv4 de la c\u00e1mara.")
    try:
        ip = IPv4Address(valor)
    except ValueError:
        raise ValueError("Ingresá la IPv4 de la cámara.") from None
    if ip.is_loopback or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
        raise ValueError("Ingresá la IP de la cámara en la red local de la Zero.")
    return str(ip)


def perfil_camara(datos):
    tipo = datos.get("tipo")
    if tipo == "rtsp":
        url = texto(datos.get("rtsp", ""), 4096, True)
        try:
            partes = urlsplit(url)
            puerto = partes.port
            ipv4_camara(partes.hostname or "")
        except ValueError:
            raise ValueError("Revisá la IP y el puerto de la URL RTSP.") from None
        if partes.scheme != "rtsp" or puerto == 0 or partes.fragment or any(c.isspace() for c in url):
            raise ValueError("Ingresá una URL rtsp:// con la IPv4 y el perfil de la cámara.")
        return {"tipo": tipo, "rtsp": url}
    if tipo not in ("tapo", "hikvision"):
        raise ValueError("Elegí el tipo de cámara.")
    ip = ipv4_camara(datos.get("ip", "").strip())
    usuario = texto(datos.get("usuario_camara", ""), 128, True)
    clave = datos.get("clave_camara", "")
    if not isinstance(clave, str) or not 1 <= len(clave) <= 256 or "\x00" in clave:
        raise ValueError("Ingresá la contraseña de la cámara.")
    puerto = int(datos.get("puerto") or "554")
    if not 1 <= puerto <= 65535:
        raise ValueError("Ingresá un puerto RTSP entre 1 y 65535.")
    principal = datos.get("perfil") == "principal"
    if tipo == "tapo":
        ruta = "stream1" if principal else "stream2"
    else:
        ruta = "Streaming/Channels/101" if principal else "Streaming/Channels/102"
    url = f"rtsp://{quote(usuario, safe='')}:{quote(clave, safe='')}@{ip}:{puerto}/{ruta}"
    return {"tipo": "tapo" if tipo == "tapo" else "rtsp", "rtsp": url}


def consultar_puestos(conexion, vivero, camara=None):
    return conexion.execute(
        """SELECT c.id, c.nombre, c.nodo_id, c.hilera, c.activa,
            EXISTS (SELECT 1 FROM capturas f WHERE f.vivero_id = c.vivero_id AND f.camara_id = c.id) AS con_fotos,
            (EXISTS (SELECT 1 FROM origenes o WHERE o.vivero_id = c.vivero_id AND o.camara_id = c.id)
             OR EXISTS (SELECT 1 FROM altas_camara a WHERE a.vivero_id = c.vivero_id
                        AND a.camara_id = c.id AND a.estado = 'ok')) AS usada
        FROM camaras c WHERE c.vivero_id = %s AND (%s::text IS NULL OR c.id = %s)
        ORDER BY c.hilera, c.id""", (vivero, camara, camara),
    ).fetchall()


def puesto_disponible(puesto, nodo):
    if puesto["con_fotos"]:
        return False
    if puesto["nodo_id"] == nodo:
        return True
    return (not puesto["activa"] and not puesto["usada"]
            and puesto["nombre"] == puesto["id"] and HILERAS.get(puesto["id"]) == puesto["hilera"])


def opciones_puestos(conexion, vivero, nodos):
    puestos = consultar_puestos(conexion, vivero)
    ocupados = {p["id"] for p in puestos}
    nuevos = [{"id": codigo, "nombre": codigo} for codigo, _ in PUESTOS if codigo not in ocupados]
    opciones = {}
    for n in nodos:
        disponibles = [p for p in puestos if puesto_disponible(p, n["id"])]
        propias = [p for p in disponibles if p["nodo_id"] == n["id"]]
        plantilla = [{**p, "de_plantilla": True} for p in disponibles if p["nodo_id"] != n["id"]]
        opciones[n["id"]] = propias + nuevos + plantilla
    return opciones


@rutas.get("/viveros/{vivero}/alta-camara")
def pagina_alta(peticion: Request, vivero: str, solicitud: str = "", nodo: str = ""):
    with conectar(cargar_configuracion()) as conexion:
        usuario = exigir(conexion, peticion, vivero, editar=True)
        registro = sitio(conexion, vivero)
        nodos = conexion.execute(
            """SELECT id, nombre, alta_en, token_sha IS NOT NULL AS enlazado
            FROM nodos WHERE vivero_id = %s AND (%s = '' OR id = %s) ORDER BY id""", (vivero, nodo, nodo),
        ).fetchall()
        if nodo and not nodos:
            raise HTTPException(404, "Zero desconocida.")
        ahora = datetime.now(UTC)
        for equipo in nodos:
            equipo["disponible"] = alta_disponible(equipo, ahora)
        puestos = opciones_puestos(conexion, vivero, nodos)
        solicitud = solicitud if ID_ALTA.fullmatch(solicitud) else ""
        reciente = conexion.execute(
            """SELECT id FROM altas_camara WHERE vivero_id = %s AND (%s = '' OR nodo_id = %s)
            AND (id = %s OR (solicitante = %s AND
                ((estado IN ('pendiente', 'en_curso') AND vence_en > now())
                 OR (tipo = 'agregar' AND terminada_en > now() - interval '2 minutes'))))
            ORDER BY (id = %s) DESC, creada_en DESC LIMIT 1""",
            (vivero, nodo, nodo, solicitud, usuario["id"], solicitud),
        ).fetchone()
    return pagina(peticion, "alta_camara.html", usuario, sitio=registro, nodos=nodos, puestos=puestos,
                  solicitud=reciente["id"] if reciente else "", nodo_fijado=nodos[0] if nodo else None,
                  titulo="Agregar cámara")


def datos_puesto(conexion, vivero, nodo, camara, nombre):
    existentes = consultar_puestos(conexion, vivero, camara)
    if existentes:
        puesto = existentes[0]
        if not puesto_disponible(puesto, nodo):
            raise HTTPException(409, "Ese puesto ya está ocupado. Elegí otro.")
        return nombre or puesto["nombre"], puesto["hilera"]
    hilera = HILERAS.get(camara)
    if hilera is None:
        raise HTTPException(409, "El puesto dejó de estar disponible. Actualizá la página.")
    return nombre or camara, hilera


@rutas.post("/api/viveros/{vivero}/altas", status_code=202)
def solicitar(peticion: Request, vivero: str, datos: FormData = Depends(datos_formulario)):
    with conectar(cargar_configuracion()) as conexion:
        usuario = exigir(conexion, peticion, vivero, editar=True, formulario=datos)
        tipo = datos.get("accion")
        if tipo not in ("buscar", "agregar", "capturar"):
            raise ValueError("Elegí buscar, agregar o tomar una foto.")
        nodo = identificador(datos.get("nodo", ""))
        camara = identificador(datos.get("camara", "")) if tipo != "buscar" else None
        perfil = perfil_camara(datos) if tipo == "agregar" else None
        nombre = texto(datos.get("nombre", ""), 80) if tipo == "agregar" else ""
        alta = secrets.token_hex(16)
        guardado = False
        vencer(conexion)
        try:
            with conexion.transaction():
                conexion.execute("SELECT id FROM viveros WHERE id = %s FOR UPDATE", (vivero,))
                equipo = fila(conexion, """SELECT alta_en, reporte, token_sha IS NOT NULL AS enlazado
                    FROM nodos WHERE vivero_id = %s AND id = %s""", (vivero, nodo), "Zero desconocida.")
                if not alta_disponible(equipo, datetime.now(UTC)):
                    raise HTTPException(409, "La Zero necesita estar conectada. Revisá su ficha en Equipos Zero.")
                if tipo == "capturar" and not captura_disponible(equipo, datetime.now(UTC)):
                    raise HTTPException(409, "Actualizá la Zero desde Configurar Zero para tomar fotos desde Central.")
                abierta = conexion.execute(
                    """SELECT id, tipo, camara_id FROM altas_camara WHERE vivero_id = %s AND (nodo_id = %s OR camara_id = %s)
                    AND estado IN ('pendiente', 'en_curso')""", (vivero, nodo, camara),
                ).fetchone()
                if abierta:
                    if tipo == "capturar" and abierta["tipo"] == tipo and abierta["camara_id"] == camara:
                        return {"id": abierta["id"]}
                    raise HTTPException(409, "Ya hay una solicitud en curso para esta Zero o puesto.")
                hilera = ""
                if tipo == "agregar":
                    nombre, hilera = datos_puesto(conexion, vivero, nodo, camara, nombre)
                    guardar_archivo(archivo_perfil(alta), json.dumps(perfil).encode("utf-8"))
                    guardado = True
                elif tipo == "capturar":
                    puesto = fila(conexion, "SELECT nombre, hilera FROM camaras WHERE vivero_id = %s AND id = %s AND nodo_id = %s",
                                  (vivero, camara, nodo), "La cámara no pertenece a esta Zero.")
                    nombre, hilera = puesto["nombre"], puesto["hilera"]
                conexion.execute(
                    """INSERT INTO altas_camara (id, vivero_id, nodo_id, camara_id, solicitante, tipo, nombre, hilera)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (alta, vivero, nodo, camara, usuario["id"], tipo, nombre, hilera),
                )
        except Exception:
            if guardado:
                retirar_perfil(alta)
            raise
    return {"id": alta}


@rutas.get("/api/viveros/{vivero}/altas/{alta}")
def leer_alta(peticion: Request, vivero: str, alta: str):
    with conectar(cargar_configuracion()) as conexion:
        exigir(conexion, peticion, vivero, editar=True)
        registro = fila(conexion, "SELECT * FROM altas_camara WHERE vivero_id = %s AND id = %s",
                        (vivero, alta), "Solicitud desconocida.")
        estado, resultado = registro["estado"], registro["resultado"]
        if estado in ABIERTA and registro["vence_en"] <= datetime.now(UTC):
            estado, resultado = "error", {"mensaje": VENCIDA}
        respuesta = {"tipo": registro["tipo"], "estado": estado, "nodo": registro["nodo_id"], **resultado}
        if estado == "ok" and registro["tipo"] in ("agregar", "capturar"):
            camara = registro["camara_id"]
            foto = conexion.execute(
                "SELECT id FROM capturas WHERE vivero_id = %s AND camara_id = %s AND capturada_en = %s",
                (vivero, camara, nombre_jpg(resultado["foto"])),
            ).fetchone()
            respuesta["camara"] = camara
            respuesta["historial"] = f"/viveros/{vivero}/camaras/{camara}"
            if foto:
                respuesta["miniatura"] = f"/miniaturas/{foto['id']}.jpg"
    return respuesta


def cerrar(conexion, alta, estado, resultado):
    conexion.execute(
        "UPDATE altas_camara SET estado = %s, resultado = %s, terminada_en = now() WHERE id = %s",
        (estado, Jsonb(resultado), alta),
    )


@rutas.post("/api/nodos/{vivero}/{nodo}/altas/tomar")
def tomar(peticion: Request, vivero: str, nodo: str):
    with conectar(cargar_configuracion()) as conexion:
        autenticar(conexion, peticion, vivero, nodo)
        with conexion.transaction():
            conexion.execute(
                """UPDATE nodos SET alta_en = now() WHERE vivero_id = %s AND id = %s
                AND (alta_en IS NULL OR alta_en < now() - interval '18 seconds')""", (vivero, nodo),
            )
            alta = conexion.execute(
                """SELECT id, tipo, camara_id, nombre FROM altas_camara WHERE vivero_id = %s AND nodo_id = %s
                AND estado IN ('pendiente', 'en_curso') AND vence_en > now() FOR UPDATE""", (vivero, nodo),
            ).fetchone()
            if alta and alta["tipo"] == "agregar":
                try:
                    alta["perfil"] = json.loads(archivo_perfil(alta["id"]).read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    cerrar(conexion, alta["id"], "error", {"mensaje": "La solicitud perdió sus datos. Volvé a intentar."})
                    alta = None
            if alta:
                conexion.execute("UPDATE altas_camara SET estado = 'en_curso' WHERE id = %s AND estado = 'pendiente'",
                                 (alta["id"],))
    return {"solicitud": alta}


def registrar_camara(conexion, alta, vivero, nodo):
    camara = alta["camara_id"]
    conexion.execute("SELECT 1 FROM camaras WHERE vivero_id = %s AND id = %s FOR UPDATE", (vivero, camara))
    existentes = consultar_puestos(conexion, vivero, camara)
    if existentes:
        puesto = existentes[0]
        if not puesto_disponible(puesto, nodo):
            return False
        registrada = conexion.execute(
            """UPDATE camaras SET nodo_id = %s, activa = true, nombre = %s, actualizada_en = now()
            WHERE vivero_id = %s AND id = %s RETURNING id""", (nodo, alta["nombre"], vivero, camara),
        ).fetchone()
        if puesto["nodo_id"] != nodo:
            conexion.execute(
                "DELETE FROM camaras_origen WHERE vivero_id = %s AND nodo_id = %s AND camara_id = %s",
                (vivero, puesto["nodo_id"], camara),
            )
    else:
        registrada = conexion.execute(
            """INSERT INTO camaras (vivero_id, id, nodo_id, nombre, hilera, activa)
            VALUES (%s, %s, %s, %s, %s, true) ON CONFLICT (vivero_id, id) DO NOTHING RETURNING id""",
            (vivero, camara, nodo, alta["nombre"], alta["hilera"]),
        ).fetchone()
    if registrada:
        registrar_origenes(conexion, vivero)
        auditar(conexion, {"id": alta["solicitante"]}, vivero, "alta de cámara probada", camara)
    return registrada is not None


def resultados_busqueda(datos):
    equipos = datos.get("camaras")
    if not isinstance(equipos, list) or len(equipos) > 64:
        raise ValueError("Resultado de búsqueda inválido.")
    encontrados = {}
    for equipo in equipos:
        if not isinstance(equipo, dict) or not isinstance(equipo.get("nombre", ""), str):
            continue
        try:
            ip = ipv4_camara(equipo.get("ip", ""))
            nombre = texto(equipo.get("nombre", ""), 80) or "Equipo ONVIF"
        except (ValueError, TypeError):
            continue
        encontrados[ip] = {"ip": ip, "nombre": nombre}
    return list(encontrados.values())


def confirmar(config, peticion, vivero, nodo, alta_id, datos):
    if not ID_ALTA.fullmatch(alta_id) or not isinstance(datos, dict) or type(datos.get("ok")) is not bool:
        raise HTTPException(400, "Resultado de alta inválido.")
    with conectar(config) as conexion:
        autenticar(conexion, peticion, vivero, nodo)
        with conexion.transaction():
            conexion.execute("SELECT id FROM viveros WHERE id = %s FOR UPDATE", (vivero,))
            alta = fila(
                conexion, "SELECT * FROM altas_camara WHERE id = %s AND vivero_id = %s AND nodo_id = %s FOR UPDATE",
                (alta_id, vivero, nodo), "Solicitud desconocida.",
            )
            estado = alta["estado"]
            if estado in ABIERTA:
                if alta["vence_en"] <= datetime.now(UTC):
                    estado, resultado = "error", {"mensaje": VENCIDA}
                elif not datos["ok"]:
                    estado = "error"
                    detalle = datos.get("mensaje")
                    mensaje = texto(detalle[:220], 220) if isinstance(detalle, str) else ""
                    resultado = {"mensaje": mensaje or "La Zero no pudo completar la prueba."}
                elif alta["tipo"] == "buscar":
                    estado, resultado = "ok", {"camaras": resultados_busqueda(datos)}
                else:
                    foto = datos.get("foto", "")
                    if not isinstance(foto, str):
                        raise ValueError("Nombre de la foto de prueba inválido.")
                    nombre_jpg(foto)
                    if alta["tipo"] == "capturar":
                        registrada = conexion.execute(
                            "SELECT 1 FROM camaras WHERE vivero_id = %s AND id = %s AND nodo_id = %s",
                            (vivero, alta["camara_id"], nodo),
                        ).fetchone()
                        if registrada:
                            auditar(conexion, {"id": alta["solicitante"]}, vivero, "tomar foto ahora", alta["camara_id"])
                    else:
                        registrada = registrar_camara(conexion, alta, vivero, nodo)
                    if registrada:
                        estado, resultado = "ok", {"foto": foto}
                    else:
                        estado, resultado = "error", {"mensaje": "El puesto se ocupó durante la prueba. Elegí otro."}
                cerrar(conexion, alta_id, estado, resultado)
        retirar_perfil(alta_id)
    return {"aceptada": estado == "ok"}


@rutas.post("/api/nodos/{vivero}/{nodo}/altas/{alta}/resultado")
async def resultado(peticion: Request, vivero: str, nodo: str, alta: str):
    datos = await json_limitado(peticion, limite=24 * 1024)
    return await run_in_threadpool(confirmar, cargar_configuracion(), peticion, vivero, nodo, alta, datos)
