from hashlib import pbkdf2_hmac, sha256
import re
import secrets

from fastapi import HTTPException

COOKIE = "mgv_sesion"
COOKIE_INGRESO = "mgv_ingreso"
SEGUNDOS_SESION = 12 * 3600
ITERACIONES = 600_000
CUENTA = re.compile(r"[a-z0-9][a-z0-9._-]{2,39}")
INEXISTENTE = "0" * 32 + "$" + "0" * 64


def huella(valor):
    return sha256(valor.encode("utf-8")).hexdigest()


def validar_cuenta(nombre):
    if not CUENTA.fullmatch(nombre):
        raise ValueError("El usuario debe tener de 3 a 40 letras minúsculas, números, puntos, guiones o guiones bajos.")
    return nombre


def codificar_clave(clave):
    if not 12 <= len(clave) <= 128:
        raise ValueError("La contraseña debe tener entre 12 y 128 caracteres.")
    sal = secrets.token_bytes(16)
    return sal.hex() + "$" + pbkdf2_hmac("sha256", clave.encode(), sal, ITERACIONES).hex()


def clave_valida(clave, guardada):
    sal, esperada = guardada.split("$", 1)
    calculada = pbkdf2_hmac("sha256", clave.encode(), bytes.fromhex(sal), ITERACIONES).hex()
    return secrets.compare_digest(calculada, esperada)


def csrf_valido(token, esperado):
    return (isinstance(token, str) and isinstance(esperado, str) and 0 < len(token) <= 128
            and token.isascii() and esperado.isascii() and secrets.compare_digest(token, esperado))


def abrir_sesion(conexion, nombre, clave, ip):
    if not CUENTA.fullmatch(nombre) or len(clave) > 128:
        return None
    conexion.execute("DELETE FROM intentos_login WHERE desde < now() - interval '1 day'")
    for limite, identificador in ((45, "ip:" + ip), (10, "usuario:" + nombre)):
        fila = conexion.execute(
            """INSERT INTO intentos_login (clave, intentos) VALUES (%s, 1)
            ON CONFLICT (clave) DO UPDATE SET
                intentos = CASE WHEN intentos_login.desde < now() - interval '15 minutes'
                    THEN 1 ELSE intentos_login.intentos + 1 END,
                desde = CASE WHEN intentos_login.desde < now() - interval '15 minutes'
                    THEN now() ELSE intentos_login.desde END
            RETURNING intentos""",
            (huella(identificador),),
        ).fetchone()
        if fila["intentos"] > limite:
            raise HTTPException(429, "Demasiados intentos. Probá de nuevo en 15 minutos.")
    usuario = conexion.execute(
        "SELECT id, clave, activo FROM usuarios WHERE nombre = %s", (nombre,),
    ).fetchone()
    correcta = clave_valida(clave, usuario["clave"] if usuario else INEXISTENTE)
    if not (usuario and correcta and usuario["activo"]):
        return None
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    with conexion.transaction():
        conexion.execute("DELETE FROM sesiones WHERE vence_en < now()")
        conexion.execute(
            """INSERT INTO sesiones (token_sha, usuario_id, csrf, vence_en)
            VALUES (%s, %s, %s, now() + interval '12 hours')""",
            (huella(token), usuario["id"], csrf),
        )
        conexion.execute("DELETE FROM intentos_login WHERE clave = %s", (huella("usuario:" + nombre),))
    return token


def usuario_actual(conexion, peticion):
    token = peticion.cookies.get(COOKIE, "")
    if len(token) != 43 or not token.isascii():
        return None
    return conexion.execute(
        """SELECT u.id, u.nombre, u.administrador, s.csrf, s.token_sha
        FROM sesiones s JOIN usuarios u ON u.id = s.usuario_id
        WHERE s.token_sha = %s AND s.vence_en > now() AND u.activo""",
        (huella(token),),
    ).fetchone()


def exigir(conexion, peticion, vivero=None, editar=False, admin=False, formulario=None):
    usuario = usuario_actual(conexion, peticion)
    if usuario is None:
        raise HTTPException(303, headers={"Location": "/ingresar"})
    if formulario is not None and not csrf_valido(formulario.get("csrf"), usuario["csrf"]):
        raise HTTPException(403, "Volvé a abrir el formulario e intentá de nuevo.")
    if admin and not usuario["administrador"]:
        raise HTTPException(403, "Esta operación requiere una cuenta administradora.")
    if vivero is not None:
        if not conexion.execute("SELECT 1 FROM viveros WHERE id = %s AND eliminado_en IS NULL", (vivero,)).fetchone():
            raise HTTPException(404, "Invernáculo desconocido.")
        permiso = conexion.execute(
            "SELECT rol FROM permisos WHERE usuario_id = %s AND vivero_id = %s", (usuario["id"], vivero),
        ).fetchone() if not usuario["administrador"] else {"rol": "tecnico"}
        if not permiso or (editar and permiso["rol"] != "tecnico"):
            raise HTTPException(403, "Tu cuenta no tiene acceso a esta operación en el invernáculo.")
        usuario["rol"] = permiso["rol"]
    return usuario


def preparar_admin(conexion, renovar=False):
    if not renovar and conexion.execute("SELECT 1 FROM usuarios WHERE administrador AND activo").fetchone():
        return None
    clave = secrets.token_urlsafe(18)
    with conexion.transaction():
        fila = conexion.execute(
            """INSERT INTO usuarios (nombre, clave, administrador) VALUES ('admin', %s, true)
            ON CONFLICT (nombre) DO UPDATE SET clave = EXCLUDED.clave, administrador = true, activo = true
            RETURNING id""",
            (codificar_clave(clave),),
        ).fetchone()
        conexion.execute("DELETE FROM sesiones WHERE usuario_id = %s", (fila["id"],))
    return clave
