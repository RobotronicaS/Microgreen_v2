from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import file_digest
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory

from .base import conectar
from .configuracion import ruta_configuracion


@contextmanager
def entorno_postgres(config):
    db = config.base_datos
    def escapar(dato):
        return str(dato).replace("\\", "\\\\").replace(":", "\\:")
    with TemporaryDirectory(prefix="mgv-pg-") as temporal:
        clave = Path(temporal) / "pgpass"
        clave.write_text(":".join(map(escapar, (
            db.host, db.puerto, "*", db.usuario, db.clave.get_secret_value(),
        ))) + "\n", encoding="utf-8")
        clave.chmod(0o600)
        ambiente = {**os.environ, "PGPASSFILE": str(clave), "PGCONNECT_TIMEOUT": "5"}
        ambiente.pop("PGPASSWORD", None)
        opciones = ["-h", db.host, "-p", str(db.puerto), "-U", db.usuario, "-w"]
        yield opciones, ambiente


def dump_base(config, destino):
    with entorno_postgres(config) as (opciones, ambiente):
        subprocess.run(
            ["pg_dump", *opciones, "-Fc", "--no-owner", "--no-acl", "-f", str(destino), config.base_datos.nombre],
            env=ambiente, check=True,
        )
    subprocess.run(["pg_restore", "--list", str(destino)], check=True, stdout=subprocess.DEVNULL)


def respaldar(config, destino=None):
    if destino is None:
        ruta = ruta_configuracion().with_name("respaldo.json")
        if not ruta.exists():
            raise ValueError("Configurá respaldo.json o indicá el destino.")
        destino = json.loads(ruta.read_text(encoding="utf-8"))["destino"]
    base = Path(destino).expanduser().resolve()
    if base.is_relative_to(config.archivo) or config.archivo.is_relative_to(base):
        raise ValueError("Elegí una carpeta de respaldo separada del archivo central.")
    if not base.is_dir():
        raise ValueError("El destino de respaldo no está disponible. Comprobá que el disco esté montado.")
    if base.stat().st_dev == config.archivo.stat().st_dev:
        raise ValueError("Montá el almacenamiento de respaldo en un disco diferente al archivo central.")
    fecha = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    anteriores = sorted(p for p in base.glob("mgv-*") if p.is_dir() and (p / "completo.json").is_file())
    with TemporaryDirectory(dir=base, prefix=".mgv-") as carpeta:
        temporal = Path(carpeta)
        dump_base(config, temporal / "base.dump")
        shutil.copyfile(ruta_configuracion(), temporal / "config.json")
        (temporal / "config.json").chmod(0o600)
        opciones = ["rsync", "-rt", "--exclude=.entrada-*", "--exclude=.recibiendo-*", "--exclude=.generando-*"]
        if anteriores:
            opciones.append(f"--link-dest={anteriores[-1] / 'archivo'}")
        subprocess.run([*opciones, str(config.archivo) + "/", str(temporal / "archivo")], check=True)
        with (temporal / "base.dump").open("rb") as archivo:
            huella = file_digest(archivo, "sha256").hexdigest()
        (temporal / "completo.json").write_text(json.dumps({"fecha_utc": fecha, "base_sha256": huella}) + "\n")
        publicado = base / f"mgv-{fecha}"
        temporal.rename(publicado)
    print(f"Respaldo generado en {publicado}.")
    return publicado


def verificar(config, archivo=None, base=None):
    raiz = Path(archivo).expanduser().resolve() if archivo else config.archivo
    cantidad, fallos = 0, 0
    with conectar(config, nombre=base) as conexion, conexion.transaction(), conexion.cursor(name="verificar_archivo") as cursor:
        cursor.execute("SELECT clave_archivo, sha256 FROM capturas ORDER BY id")
        for captura in cursor:
            original = raiz / "originales" / captura["clave_archivo"]
            miniatura = raiz / "miniaturas" / captura["clave_archivo"]
            cantidad += 1
            try:
                with original.open("rb") as archivo_foto:
                    correcto = file_digest(archivo_foto, "sha256").hexdigest() == captura["sha256"]
                correcto = correcto and miniatura.is_file()
            except OSError:
                correcto = False
            if not correcto:
                fallos += 1
                if fallos <= 20:
                    print(f"Revisar {captura['clave_archivo']}")
    print(f"{cantidad} JPG verificados · {fallos} faltantes o con huella distinta.")
    return fallos == 0
