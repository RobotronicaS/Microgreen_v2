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
    import fcntl

    with ruta_configuracion().with_name(".respaldo.lock").open("a") as bloqueo:
        try:
            fcntl.flock(bloqueo, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Ya hay un respaldo en curso.") from None
        return crear_respaldo(config, destino)


def crear_respaldo(config, destino=None):
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
    fecha = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    anteriores = sorted(p for p in base.glob("mgv-*") if p.is_dir() and (p / "completo.json").is_file())
    with TemporaryDirectory(dir=base, prefix=".mgv-") as carpeta:
        temporal = Path(carpeta)
        dump_base(config, temporal / "base.dump")
        shutil.copyfile(ruta_configuracion(), temporal / "config.json")
        (temporal / "config.json").chmod(0o600)
        configuraciones = {}
        for nombre in ("avisos.json", "respaldo.json"):
            origen = ruta_configuracion().with_name(nombre)
            if origen.is_file():
                copia = temporal / nombre
                shutil.copyfile(origen, copia)
                copia.chmod(0o600)
                with copia.open("rb") as entrada:
                    configuraciones[nombre] = file_digest(entrada, "sha256").hexdigest()
        opciones = ["rsync", "-rt", "--exclude=.entrada-*", "--exclude=.recibiendo-*", "--exclude=.generando-*"]
        if anteriores:
            opciones.append(f"--link-dest={anteriores[-1] / 'archivo'}")
        subprocess.run([*opciones, str(config.archivo) + "/", str(temporal / "archivo")], check=True)
        with (temporal / "base.dump").open("rb") as archivo:
            huella = file_digest(archivo, "sha256").hexdigest()
        cantidad, peso = 0, 0
        manifiesto = temporal / "archivos.jsonl"
        with manifiesto.open("w", encoding="utf-8") as salida:
            for archivo in sorted((temporal / "archivo").rglob("*")):
                if not archivo.is_file() or archivo.is_symlink():
                    continue
                with archivo.open("rb") as entrada:
                    sha = file_digest(entrada, "sha256").hexdigest()
                tamanio = archivo.stat().st_size
                salida.write(json.dumps({"ruta": archivo.relative_to(temporal / "archivo").as_posix(),
                                         "sha256": sha, "bytes": tamanio}) + "\n")
                cantidad += 1
                peso += tamanio
        with manifiesto.open("rb") as entrada:
            manifiesto_sha = file_digest(entrada, "sha256").hexdigest()
        with (temporal / "config.json").open("rb") as entrada:
            config_sha = file_digest(entrada, "sha256").hexdigest()
        (temporal / "completo.json").write_text(json.dumps({
            "fecha_utc": fecha, "base_sha256": huella, "config_sha256": config_sha,
            "manifiesto_sha256": manifiesto_sha, "archivos": cantidad, "bytes": peso,
            "configuraciones_sha256": configuraciones,
        }) + "\n", encoding="utf-8")
        publicado = base / f"mgv-{fecha}"
        temporal.rename(publicado)
    print(f"Respaldo generado en {publicado}.")
    return publicado


def ultimo_respaldo(destino):
    base = Path(destino).expanduser().resolve()
    candidatos = sorted(p for p in base.glob("mgv-*") if p.is_dir() and not p.is_symlink()
                        and (p / "completo.json").is_file())
    return candidatos[-1] if candidatos else None


def verificar_respaldo(ruta):
    ruta = Path(ruta).resolve()
    datos = json.loads((ruta / "completo.json").read_text(encoding="utf-8"))
    with (ruta / "base.dump").open("rb") as entrada:
        if file_digest(entrada, "sha256").hexdigest() != datos["base_sha256"]:
            raise ValueError("La copia de la base tiene una huella distinta.")
    subprocess.run(["pg_restore", "--list", str(ruta / "base.dump")], check=True, stdout=subprocess.DEVNULL)
    if "manifiesto_sha256" not in datos:
        return {"completa": False, "mensaje": "Base verificada. Este respaldo anterior requiere una prueba de restauración para revisar las fotografías."}
    for nombre, campo in (("archivos.jsonl", "manifiesto_sha256"), ("config.json", "config_sha256")):
        with (ruta / nombre).open("rb") as entrada:
            if file_digest(entrada, "sha256").hexdigest() != datos[campo]:
                raise ValueError("El respaldo tiene un archivo de control modificado.")
    for nombre, sha in datos.get("configuraciones_sha256", {}).items():
        if nombre not in ("avisos.json", "respaldo.json"):
            raise ValueError("La lista de configuraciones del respaldo es inválida.")
        with (ruta / nombre).open("rb") as entrada:
            if file_digest(entrada, "sha256").hexdigest() != sha:
                raise ValueError("La configuración guardada en el respaldo tiene una huella distinta.")
    raiz = ruta / "archivo"
    cantidad = 0
    with (ruta / "archivos.jsonl").open(encoding="utf-8") as manifiesto:
        for linea in manifiesto:
            registro = json.loads(linea)
            archivo = (raiz / registro["ruta"]).resolve()
            if not archivo.is_relative_to(raiz) or not archivo.is_file():
                raise ValueError("Falta un archivo del respaldo o su ruta es inválida.")
            with archivo.open("rb") as entrada:
                sha = file_digest(entrada, "sha256").hexdigest()
            if sha != registro["sha256"] or archivo.stat().st_size != registro["bytes"]:
                raise ValueError("Se detectó un archivo dañado en el respaldo.")
            cantidad += 1
    if cantidad != datos["archivos"]:
        raise ValueError("La cantidad de archivos del respaldo es inválida.")
    return {"completa": True, "archivos": cantidad,
            "mensaje": f"Base legible y {cantidad} archivos verificados por SHA-256."}


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
