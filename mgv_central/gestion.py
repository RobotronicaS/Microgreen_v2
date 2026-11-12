import json
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.datastructures import FormData

from .acceso import codificar_clave, clave_valida, exigir, huella, validar_cuenta
from .base import auditar, conectar
from .configuracion import cargar_configuracion
from .dominio import INTERVALOS, PUESTOS, hora_formulario, identificador, texto, zona
from .web import datos_formulario, fila, pagina, sitio, volver

rutas = APIRouter()


@rutas.get("/gestion")
def gestion(peticion: Request):
    with conectar(cargar_configuracion()) as conexion:
        usuario = exigir(conexion, peticion, admin=True)
        sitios = conexion.execute("SELECT * FROM viveros ORDER BY nombre").fetchall()
        cuentas = conexion.execute("SELECT id, nombre, activo, administrador FROM usuarios ORDER BY nombre").fetchall()
        permisos = conexion.execute(
            """SELECT p.*, u.nombre AS usuario, v.nombre AS sitio FROM permisos p
            JOIN usuarios u ON u.id = p.usuario_id JOIN viveros v ON v.id = p.vivero_id
            ORDER BY u.nombre, v.nombre""",
        ).fetchall()
        historial = conexion.execute(
            """SELECT a.*, u.nombre FROM auditoria a LEFT JOIN usuarios u ON u.id = a.usuario_id
            ORDER BY a.id DESC LIMIT 40""",
        ).fetchall()
    return pagina(peticion, "gestion.html", usuario, sitios=sitios, cuentas=cuentas, permisos=permisos,
                  historial=historial, titulo="Administración")


def plantilla_puestos(conexion, vivero, nodo):
    with conexion.cursor() as cursor:
        cursor.executemany(
            """INSERT INTO camaras (vivero_id, id, nodo_id, nombre, hilera, activa)
            VALUES (%s, %s, %s, %s, %s, false) ON CONFLICT DO NOTHING""",
            [(vivero, camara, nodo, camara, hilera) for camara, hilera in PUESTOS],
        )
    registrar_origenes(conexion, vivero)


def registrar_origenes(conexion, vivero):
    conexion.execute(
        """INSERT INTO camaras_origen (vivero_id, nodo_id, camara_id)
        SELECT vivero_id, nodo_id, id FROM camaras WHERE vivero_id = %s ON CONFLICT DO NOTHING""",
        (vivero,),
    )


@rutas.post("/gestion/viveros")
def crear_vivero(peticion: Request, datos: FormData = Depends(datos_formulario)):
    with conectar(cargar_configuracion()) as conexion:
        usuario = exigir(conexion, peticion, admin=True, formulario=datos)
        vivero = identificador(datos.get("id", "")).lower()
        nodo = identificador(datos.get("nodo", "zero01")).lower()
        nombre = texto(datos.get("nombre", ""), 80, True)
        ubicacion = texto(datos.get("ubicacion", ""), 120)
        zona_nombre = zona(datos.get("zona", "America/Montevideo")).key
        with conexion.transaction():
            conexion.execute("INSERT INTO viveros (id, nombre, zona, ubicacion) VALUES (%s, %s, %s, %s)",
                             (vivero, nombre, zona_nombre, ubicacion))
            conexion.execute("INSERT INTO nodos (vivero_id, id, nombre) VALUES (%s, %s, %s)", (vivero, nodo, nodo))
            if datos.get("plantilla") == "si":
                plantilla_puestos(conexion, vivero, nodo)
            auditar(conexion, usuario, vivero, "crear invernáculo")
    return volver(f"/viveros/{vivero}/ajustes")


@rutas.get("/viveros/{vivero}/ajustes")
def ajustes(peticion: Request, vivero: str):
    with conectar(cargar_configuracion()) as conexion:
        usuario = exigir(conexion, peticion, vivero, editar=True)
        registro = sitio(conexion, vivero)
        nodos = conexion.execute(
            """SELECT id, nombre, entrada_legacy, ultimo_contacto, token_sha IS NOT NULL AS enlazado
            FROM nodos WHERE vivero_id = %s ORDER BY id""", (vivero,),
        ).fetchall()
        camaras = conexion.execute("SELECT * FROM camaras WHERE vivero_id = %s ORDER BY hilera, id", (vivero,)).fetchall()
    return pagina(peticion, "ajustes.html", usuario, sitio=registro, nodos=nodos, camaras=camaras,
                  intervalos=INTERVALOS, titulo="Ajustes de invernáculo")


@rutas.post("/viveros/{vivero}/ajustes")
def ajustar(peticion: Request, vivero: str, datos: FormData = Depends(datos_formulario)):
    with conectar(cargar_configuracion()) as conexion:
        usuario = exigir(conexion, peticion, vivero, editar=True, formulario=datos)
        with conexion.transaction():
            conexion.execute("UPDATE viveros SET nombre = %s, zona = %s, ubicacion = %s WHERE id = %s", (
                texto(datos.get("nombre", ""), 80, True), zona(datos.get("zona", "")).key,
                texto(datos.get("ubicacion", ""), 120), vivero,
            ))
            auditar(conexion, usuario, vivero, "ajustar invernáculo")
    return volver(f"/viveros/{vivero}/ajustes")


@rutas.post("/viveros/{vivero}/nodos")
def crear_nodo(peticion: Request, vivero: str, datos: FormData = Depends(datos_formulario)):
    with conectar(cargar_configuracion()) as conexion:
        usuario = exigir(conexion, peticion, vivero, admin=True, formulario=datos)
        nodo = identificador(datos.get("id", "")).lower()
        with conexion.transaction():
            conexion.execute("INSERT INTO nodos (vivero_id, id, nombre) VALUES (%s, %s, %s)",
                             (vivero, nodo, texto(datos.get("nombre", ""), 80, True)))
            auditar(conexion, usuario, vivero, "crear nodo", nodo)
    return volver(f"/viveros/{vivero}/ajustes")


@rutas.post("/viveros/{vivero}/plantilla")
def completar(peticion: Request, vivero: str, datos: FormData = Depends(datos_formulario)):
    with conectar(cargar_configuracion()) as conexion:
        usuario = exigir(conexion, peticion, vivero, editar=True, formulario=datos)
        nodo = datos.get("nodo", "")
        fila(conexion, "SELECT 1 FROM nodos WHERE vivero_id = %s AND id = %s", (vivero, nodo), "Nodo desconocido.")
        with conexion.transaction():
            plantilla_puestos(conexion, vivero, nodo)
            auditar(conexion, usuario, vivero, "completar 15 puestos", nodo)
    return volver(f"/viveros/{vivero}/ajustes")


@rutas.post("/viveros/{vivero}/nodos/{nodo}/credencial")
def credencial(peticion: Request, vivero: str, nodo: str, datos: FormData = Depends(datos_formulario)):
    with conectar(cargar_configuracion()) as conexion:
        usuario = exigir(conexion, peticion, vivero, admin=True, formulario=datos)
        token = secrets.token_urlsafe(32)
        with conexion.transaction():
            fila(conexion, """UPDATE nodos SET token_sha = %s, autorizado_en = now(), ultimo_contacto = NULL, alta_en = NULL, reporte = '{}'
                 WHERE vivero_id = %s AND id = %s RETURNING id""", (huella(token), vivero, nodo), "Nodo desconocido.")
            auditar(conexion, usuario, vivero, "emitir credencial", nodo)
    config = json.dumps({"url": "https://TU-NODO.ts.net:8766", "vivero": vivero, "nodo": nodo, "token": token}, indent=2)
    return pagina(peticion, "credencial.html", usuario, config=config, vivero=vivero, nodo=nodo, titulo="Credencial del nodo")


@rutas.post("/viveros/{vivero}/camaras")
def guardar_camara(peticion: Request, vivero: str, datos: FormData = Depends(datos_formulario)):
    with conectar(cargar_configuracion()) as conexion:
        usuario = exigir(conexion, peticion, vivero, editar=True, formulario=datos)
        camara = identificador(datos.get("id", ""))
        nodo = datos.get("nodo", "")
        fila(conexion, "SELECT 1 FROM nodos WHERE vivero_id = %s AND id = %s", (vivero, nodo), "Nodo desconocido.")
        intervalo, atraso = int(datos.get("intervalo", "30")), int(datos.get("atraso", "65"))
        if intervalo not in INTERVALOS or not 5 <= atraso <= 1440:
            raise ValueError("Revisá la frecuencia y el atraso, expresados en minutos.")
        with conexion.transaction():
            conexion.execute(
                """INSERT INTO camaras (vivero_id, id, nodo_id, nombre, hilera, activa, intervalo, atraso_minutos, desde, hasta)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (vivero_id, id) DO UPDATE SET nodo_id = EXCLUDED.nodo_id, nombre = EXCLUDED.nombre,
                  hilera = EXCLUDED.hilera, activa = EXCLUDED.activa, intervalo = EXCLUDED.intervalo,
                  atraso_minutos = EXCLUDED.atraso_minutos, desde = EXCLUDED.desde, hasta = EXCLUDED.hasta,
                  actualizada_en = CASE WHEN (camaras.nodo_id, camaras.activa, camaras.intervalo, camaras.desde, camaras.hasta)
                    IS DISTINCT FROM (EXCLUDED.nodo_id, EXCLUDED.activa, EXCLUDED.intervalo, EXCLUDED.desde, EXCLUDED.hasta)
                    THEN now() ELSE camaras.actualizada_en END""",
                (vivero, camara, nodo, texto(datos.get("nombre", ""), 80, True), texto(datos.get("hilera", ""), 25),
                 datos.get("activa") == "si", intervalo, atraso,
                 hora_formulario(datos.get("desde", "00:00")), hora_formulario(datos.get("hasta", "00:00"))),
            )
            registrar_origenes(conexion, vivero)
            auditar(conexion, usuario, vivero, "guardar cámara", camara)
    return volver(f"/viveros/{vivero}/ajustes")


@rutas.post("/gestion/usuarios")
def crear_usuario(peticion: Request, datos: FormData = Depends(datos_formulario)):
    with conectar(cargar_configuracion()) as conexion:
        usuario = exigir(conexion, peticion, admin=True, formulario=datos)
        nombre = validar_cuenta(datos.get("nombre", "").strip())
        with conexion.transaction():
            conexion.execute("INSERT INTO usuarios (nombre, clave, administrador) VALUES (%s, %s, %s)", (
                nombre, codificar_clave(datos.get("clave", "")), datos.get("administrador") == "si",
            ))
            auditar(conexion, usuario, None, "crear usuario", nombre)
    return volver("/gestion")


@rutas.post("/gestion/permisos")
def otorgar_permiso(peticion: Request, datos: FormData = Depends(datos_formulario)):
    with conectar(cargar_configuracion()) as conexion:
        usuario = exigir(conexion, peticion, admin=True, formulario=datos)
        cuenta, vivero, rol = int(datos.get("cuenta", "")), datos.get("vivero", ""), datos.get("rol", "")
        sitio(conexion, vivero)
        registro = fila(conexion, "SELECT nombre FROM usuarios WHERE id = %s", (cuenta,), "Cuenta desconocida.")
        with conexion.transaction():
            if rol in ("tecnico", "consulta"):
                conexion.execute(
                    """INSERT INTO permisos (usuario_id, vivero_id, rol) VALUES (%s, %s, %s)
                    ON CONFLICT (usuario_id, vivero_id) DO UPDATE SET rol = EXCLUDED.rol""", (cuenta, vivero, rol),
                )
            elif rol == "retirar":
                conexion.execute("DELETE FROM permisos WHERE usuario_id = %s AND vivero_id = %s", (cuenta, vivero))
            else:
                raise ValueError("Elegí un permiso válido.")
            auditar(conexion, usuario, vivero, "cambiar permiso", f"{registro['nombre']} {rol}")
    return volver("/gestion")


@rutas.post("/gestion/usuarios/{cuenta}/estado")
def estado_usuario(peticion: Request, cuenta: int, datos: FormData = Depends(datos_formulario)):
    with conectar(cargar_configuracion()) as conexion:
        usuario = exigir(conexion, peticion, admin=True, formulario=datos)
        if cuenta == usuario["id"]:
            raise ValueError("Usá otra cuenta administradora para suspender esta cuenta.")
        activo = datos.get("activo") == "si"
        with conexion.transaction():
            registro = fila(conexion, "UPDATE usuarios SET activo = %s WHERE id = %s RETURNING nombre",
                            (activo, cuenta), "Cuenta desconocida.")
            if not activo:
                conexion.execute("DELETE FROM sesiones WHERE usuario_id = %s", (cuenta,))
            auditar(conexion, usuario, None, "activar usuario" if activo else "suspender usuario", registro["nombre"])
    return volver("/gestion")


@rutas.get("/cuenta")
def cuenta_actual(peticion: Request):
    with conectar(cargar_configuracion()) as conexion:
        usuario = exigir(conexion, peticion)
    return pagina(peticion, "cuenta.html", usuario, titulo="Mi cuenta")


@rutas.post("/cuenta")
def cambiar_clave(peticion: Request, datos: FormData = Depends(datos_formulario)):
    with conectar(cargar_configuracion()) as conexion:
        usuario = exigir(conexion, peticion, formulario=datos)
        anterior = conexion.execute("SELECT clave FROM usuarios WHERE id = %s", (usuario["id"],)).fetchone()["clave"]
        clave = datos.get("anterior", "")
        if len(clave) > 128 or not clave_valida(clave, anterior):
            raise HTTPException(403, "La contraseña actual es incorrecta.")
        nueva = codificar_clave(datos.get("nueva", ""))
        with conexion.transaction():
            conexion.execute("UPDATE usuarios SET clave = %s WHERE id = %s", (nueva, usuario["id"]))
            conexion.execute("DELETE FROM sesiones WHERE usuario_id = %s AND token_sha <> %s",
                             (usuario["id"], usuario["token_sha"]))
            auditar(conexion, usuario, None, "cambiar contraseña")
    return pagina(peticion, "cuenta.html", usuario, cambiada=True, titulo="Mi cuenta")
