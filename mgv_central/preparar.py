import json
import os
from pathlib import Path
import secrets
import subprocess

from psycopg import Error, sql

from .acceso import preparar_admin
from .base import conectar, crear_esquema, registrar_catalogo
from .configuracion import RAIZ, cargar_configuracion, ruta_configuracion


def crear_configuracion():
    ruta = ruta_configuracion()
    if not ruta.exists():
        contenido = {
            # Las variables MGV_CENTRAL_DB_* y MGV_CENTRAL_ARCHIVO solo se usan al crear config.json
            # (por ejemplo, desde Docker Compose). Después manda el archivo.
            "base_datos": {
                "host": os.getenv("MGV_CENTRAL_DB_HOST", "127.0.0.1"),
                "puerto": int(os.getenv("MGV_CENTRAL_DB_PUERTO", "5432")),
                "nombre": os.getenv("MGV_CENTRAL_DB_NOMBRE", "microgreen_central"),
                "usuario": os.getenv("MGV_CENTRAL_DB_USUARIO", "microgreen_central"),
                "clave": os.getenv("MGV_CENTRAL_DB_CLAVE") or secrets.token_urlsafe(36),
            },
            "archivo": os.getenv("MGV_CENTRAL_ARCHIVO", str(RAIZ / "datos" / "archivo")),
            "zona_horaria": "America/Montevideo",
            "viveros": [
                {
                    "id": "piloto", "nombre": "Vivero piloto",
                    "nodos": [
                        {
                            "id": "zero01", "entrada": str(Path.home() / "microgreen-inbox" / "zero01"),
                            "camaras": [{"id": "H1-C01", "nombre": "H1-C01", "hilera": "1", "atraso_minutos": 65}],
                        },
                    ],
                },
            ],
        }
        # Solo importar el catálogo piloto cuando ya existe una entrada de fotos real.
        entrada_legacy = Path.home() / "microgreen-inbox" / "zero01"
        if not entrada_legacy.is_dir() or not next(entrada_legacy.glob("*/*/*.jpg"), None):
            contenido["viveros"] = []
        ruta.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with ruta.open("x", encoding="utf-8") as salida:
            json.dump(contenido, salida, ensure_ascii=False, indent=2)
            salida.write("\n")
    ruta.chmod(0o600)
    return cargar_configuracion()


def administrar(config, instruccion):
    comando = [
        "sudo", "-u", "postgres", "--", "psql", "-X", "-qAt",
        "--port", str(config.base_datos.puerto), "--dbname", "postgres", "-v", "ON_ERROR_STOP=1",
    ]
    salida = subprocess.run(comando, input=instruccion.as_string() + "\n", text=True, capture_output=True)
    if salida.returncode:
        clave = config.base_datos.clave.get_secret_value()
        detalle = salida.stderr.strip().replace(clave, "[clave]") if clave else salida.stderr.strip()
        raise ValueError(f"PostgreSQL rechazó la preparación. {detalle}")
    return salida.stdout.strip()


def preparar_postgres(config):
    db = config.base_datos
    if db.host not in ("127.0.0.1", "localhost"):
        # Base administrada fuera de este equipo (por ejemplo, el contenedor de Docker Compose):
        # la cuenta y la base ya existen; solo se comprueba el acceso.
        try:
            with conectar(config):
                pass
        except Error as error:
            raise ValueError(
                f"No se pudo conectar a PostgreSQL en {db.host}:{db.puerto}. "
                "Revisá que la base exista y las credenciales de config.json."
            ) from error
        return

    nombre, usuario = sql.Literal(db.nombre), sql.Literal(db.usuario)
    propietario = administrar(
        config, sql.SQL("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = {};").format(nombre),
    )
    if propietario and propietario != db.usuario:
        raise ValueError(f"La base {db.nombre} pertenece a otra cuenta. Elegí un nombre nuevo en config.json.")

    rol = administrar(config, sql.SQL(
        """
        SELECT CASE WHEN rolcanlogin AND NOT rolsuper AND NOT rolcreatedb
            AND NOT rolcreaterole AND NOT rolreplication AND NOT rolbypassrls
            AND NOT EXISTS (SELECT 1 FROM pg_auth_members WHERE member = pg_roles.oid)
            THEN 'limitado' ELSE 'privilegios' END
        FROM pg_roles WHERE rolname = {};
        """
    ).format(usuario))
    if rol == "privilegios":
        raise ValueError(f"La cuenta de base {db.usuario} tiene privilegios ajenos a la aplicación.")
    if rol == "limitado":
        try:
            with conectar(config, db.nombre if propietario else "postgres"):
                pass
        except Error as error:
            raise ValueError(
                f"Revisá las credenciales de {db.usuario} en config.json y el acceso local a PostgreSQL."
            ) from error
    else:
        administrar(config, sql.SQL(
            """
            SET password_encryption = 'scram-sha-256';
            CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {};
            """
        ).format(sql.Identifier(db.usuario), sql.Literal(db.clave.get_secret_value())))
    if not propietario:
        administrar(config, sql.SQL(
            "CREATE DATABASE {base} OWNER {usuario}; REVOKE CONNECT ON DATABASE {base} FROM PUBLIC;"
        ).format(base=sql.Identifier(db.nombre), usuario=sql.Identifier(db.usuario)))


def preparar():
    config = crear_configuracion()
    preparar_postgres(config)
    config.archivo.mkdir(parents=True, exist_ok=True, mode=0o700)
    with conectar(config) as conexion:
        crear_esquema(conexion)
        registrar_catalogo(conexion, config)
        conexion.execute(
            """INSERT INTO trabajos (vivero_id, tipo, clave, datos)
            SELECT vivero_id, 'analisis', 'foto:' || id, jsonb_build_object('captura', id)
            FROM capturas ON CONFLICT (clave) DO NOTHING""",
        )
        clave = preparar_admin(conexion)
    if clave:
        print(f"Primer acceso al panel · Usuario admin · Contraseña {clave}", flush=True)
    print("Base y catálogo de MicroGreenVision Central preparados.", flush=True)

