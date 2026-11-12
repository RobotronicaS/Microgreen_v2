from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import file_digest, sha256
from io import BytesIO
import os
from pathlib import Path
import re
import secrets
import stat
from tempfile import NamedTemporaryFile

from PIL import Image, ImageOps, UnidentifiedImageError
from psycopg.types.json import Jsonb

from .base import conectar


DIA = re.compile(r"\d{4}-\d{2}-\d{2}")
JPG = re.compile(r"\d{8}T\d{6}_\d{6}Z\.jpg")
TOPE_POR_CAMARA = 8


class FotoInvalida(ValueError):
    pass


@contextmanager
def directorio(ruta, padre=None):
    fd = os.open(ruta, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=padre)
    try:
        yield fd
    finally:
        os.close(fd)


def firma(datos):
    return datos.st_mtime_ns, datos.st_size


def leer_jpg(carpeta, nombre, esperado, max_bytes):
    fd = os.open(nombre, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=carpeta)
    with os.fdopen(fd, "rb") as entrada:
        inicial = os.fstat(entrada.fileno())
        if not stat.S_ISREG(inicial.st_mode) or firma(inicial) != esperado:
            raise OSError("El archivo cambió durante la lectura.")
        if not 4 <= inicial.st_size <= max_bytes:
            raise FotoInvalida("Tamaño de JPG fuera del límite configurado.")
        contenido = entrada.read(max_bytes + 1)
        if firma(os.fstat(entrada.fileno())) != esperado or len(contenido) != inicial.st_size:
            raise OSError("El archivo cambió durante la lectura.")
    return contenido


def preparar_foto(contenido, max_megapixeles):
    if not contenido.startswith(b"\xff\xd8") or not contenido.endswith(b"\xff\xd9"):
        raise FotoInvalida("JPG incompleto o inválido.")
    try:
        with Image.open(BytesIO(contenido)) as imagen:
            ancho, alto = imagen.size
            if imagen.format != "JPEG" or ancho * alto > max_megapixeles * 1_000_000:
                raise FotoInvalida("Formato o resolución fuera del límite configurado.")
            imagen.load()
            miniatura = ImageOps.exif_transpose(imagen)
            miniatura.thumbnail((512, 360), Image.Resampling.LANCZOS)
            salida = BytesIO()
            miniatura.convert("RGB").save(salida, "JPEG", quality=82)
            return ancho, alto, salida.getvalue()
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as error:
        raise FotoInvalida("El JPG no se pudo decodificar.") from error


def guardar_archivo(ruta, contenido, huella=None):
    crear_directorio(ruta.parent)
    if ruta.is_symlink() or ruta.exists() and not ruta.is_file():
        raise FotoInvalida("El destino del archivo no es un JPG regular.")
    if ruta.exists():
        if huella and not verificar_huella(ruta, huella):
            raise FotoInvalida("La ruta del archivo ya contiene una captura distinta.")
        return

    temporal = None
    try:
        with NamedTemporaryFile(dir=ruta.parent, prefix=".entrada-", delete=False) as salida:
            temporal = Path(salida.name)
            salida.write(contenido)
            salida.flush()
            os.fsync(salida.fileno())
        try:
            os.link(temporal, ruta, follow_symlinks=False)
        except FileExistsError as error:
            raise OSError("La ruta apareció durante la escritura. Se reintentará.") from error
        sincronizar(ruta.parent)
    finally:
        if temporal:
            temporal.unlink(missing_ok=True)


def sincronizar(ruta):
    with directorio(ruta) as fd:
        os.fsync(fd)


def verificar_huella(ruta, huella):
    if ruta.is_symlink() or not ruta.is_file():
        return False
    try:
        with ruta.open("rb") as archivo:
            return file_digest(archivo, "sha256").hexdigest() == huella
    except FileNotFoundError:
        return False


def crear_directorio(ruta):
    if ruta.is_dir() and not ruta.is_symlink():
        return
    crear_directorio(ruta.parent)
    try:
        ruta.mkdir(mode=0o700)
    except FileExistsError:
        if ruta.is_symlink() or not ruta.is_dir():
            raise OSError("Directorio de archivo inválido.") from None
    sincronizar(ruta.parent)


def crear_bandeja(config, vivero, nodo, camara, dia):
    ruta = config.archivo
    for parte in ("bandeja", vivero, nodo, camara, dia):
        padre, ruta = ruta, ruta / parte
        try:
            ruta.mkdir(mode=0o700)
        except FileExistsError:
            if ruta.is_symlink() or not ruta.is_dir():
                raise OSError("Directorio de recepción inválido.")
        else:
            sincronizar(padre)
    return ruta


def registrar_origen(conexion, origen, ruta, datos, captura_id=None, problema=None, rechazada=None):
    conexion.execute(
        """
        INSERT INTO origenes
          (vivero_id, nodo_id, camara_id, ruta, mtime_ns, bytes, captura_id, problema, copia_rechazada)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (vivero_id, nodo_id, camara_id, ruta) DO UPDATE
        SET mtime_ns = EXCLUDED.mtime_ns, bytes = EXCLUDED.bytes,
            captura_id = EXCLUDED.captura_id, problema = EXCLUDED.problema,
            copia_rechazada = EXCLUDED.copia_rechazada
        """,
        (*origen, ruta, *datos, captura_id, problema, rechazada),
    )


def incorporar(conexion, config, origen, dia, nombre, carpeta, datos, recibida_en):
    vivero, _, camara = origen
    try:
        momento = datetime.strptime(nombre, "%Y%m%dT%H%M%S_%fZ.jpg").replace(tzinfo=UTC)
    except ValueError as error:
        raise FotoInvalida("La fecha del nombre del JPG es inválida.") from error
    if momento.date().isoformat() != dia:
        raise FotoInvalida("La carpeta no coincide con la fecha UTC del JPG.")

    contenido = leer_jpg(carpeta, nombre, datos, config.max_jpg_mib * 1024 * 1024)
    huella = sha256(contenido).hexdigest()
    anterior = conexion.execute(
        """
        SELECT id, sha256, clave_archivo FROM capturas
        WHERE vivero_id = %s AND camara_id = %s AND capturada_en = %s
        """,
        (vivero, camara, momento),
    ).fetchone()
    if anterior and anterior["sha256"] != huella:
        raise FotoInvalida("Ya hay una captura distinta para esa cámara y hora.")

    clave = anterior["clave_archivo"] if anterior else f"{vivero}/{camara}/{dia}/{nombre}"
    original = config.archivo / "originales" / clave
    miniatura_ruta = config.archivo / "miniaturas" / clave
    if anterior and original.exists() and not verificar_huella(original, huella):
        raise FotoInvalida("La copia archivada difiere de la huella registrada.")
    if not anterior or not original.is_file() or not miniatura_ruta.is_file():
        ancho, alto, miniatura = preparar_foto(contenido, config.max_megapixeles)
        guardar_archivo(original, contenido, huella)
        guardar_archivo(miniatura_ruta, miniatura)
    with conexion.transaction():
        fila = anterior or conexion.execute(
            """
            INSERT INTO capturas
              (vivero_id, camara_id, capturada_en, clave_archivo, sha256, bytes, ancho, alto, recibida_en)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (vivero, camara, momento, clave, huella, len(contenido), ancho, alto, recibida_en),
        ).fetchone()
        registrar_origen(conexion, origen, f"{dia}/{nombre}", datos, captura_id=fila["id"])
        if anterior is None:
            encolar_analisis(conexion, vivero, fila["id"])
    return anterior is None


def encolar_analisis(conexion, vivero, captura_id):
    conexion.execute(
        """INSERT INTO trabajos (vivero_id, tipo, clave, datos)
        VALUES (%s, 'analisis', %s, %s) ON CONFLICT (clave) DO NOTHING""",
        (vivero, f"foto:{captura_id}", Jsonb({"captura": captura_id})),
    )


def registrar_error(resultado, detalle):
    resultado["errores"] += 1
    if not resultado["detalle"]:
        resultado["detalle"] = detalle


def listar_dias(fd):
    with os.scandir(fd) as fechas:
        return sorted(
            (e.name for e in fechas if DIA.fullmatch(e.name) and e.is_dir(follow_symlinks=False)),
            reverse=True,
        )


def listar_jpg(fd):
    with os.scandir(fd) as entradas:
        return sorted(
            (e.name for e in entradas if JPG.fullmatch(e.name) and e.is_file(follow_symlinks=False)),
            reverse=True,
        )


def procesar_archivo(conexion, config, origen, dia, nombre, carpeta, cuarentena, resultado, http):
    info = os.stat(nombre, dir_fd=carpeta, follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode):
        return
    datos = firma(info)
    try:
        recibida = datetime.fromtimestamp(info.st_mtime, UTC) if http else datetime.now(UTC)
        resultado["nuevas"] += incorporar(conexion, config, origen, dia, nombre, carpeta, datos, recibida)
    except FotoInvalida as error:
        destino = cuarentena / origen[2] / dia / f"{secrets.token_hex(6)}-{nombre}"
        crear_directorio(destino.parent)
        registrar_origen(conexion, origen, f"{dia}/{nombre}", datos, problema=str(error), rechazada=str(destino))
        os.rename(nombre, destino, src_dir_fd=carpeta)
        sincronizar(destino.parent)
    else:
        os.unlink(nombre, dir_fd=carpeta)
    os.fsync(carpeta)


def retirar_vacio(dia, fd):
    try:
        os.rmdir(dia, dir_fd=fd)
    except OSError:
        # Puede haber una transferencia temporal o una nueva foto en la carpeta.
        pass


def revisar_camara(conexion, config, origen, raiz, resultado, limite, legacy=None, http=False):
    procesadas = 0
    if not raiz.exists():
        return procesadas
    with directorio(raiz) as fd, directorio(origen[2], fd) as camara:
        dias = listar_dias(camara)
        for dia in dias:
            if procesadas >= limite:
                break
            procesadas += revisar_dia(
                conexion, config, origen, camara, dia, raiz, resultado, limite - procesadas, legacy, http,
            )
            # Conserva el directorio del día corriente mientras llegan transferencias.
            if dia < datetime.now(UTC).date().isoformat():
                retirar_vacio(dia, camara)
    return procesadas


def revisar_dia(conexion, config, origen, camara, dia, raiz, resultado, limite, legacy, http):
    procesadas = 0
    try:
        with directorio(dia, camara) as carpeta:
            for nombre in listar_jpg(carpeta)[:limite]:
                procesadas += 1
                try:
                    cuarentena = raiz.parent / (raiz.name + "-rechazados")
                    if legacy is None:
                        procesar_archivo(conexion, config, origen, dia, nombre, carpeta, cuarentena, resultado, http)
                        continue
                    destino = legacy / origen[2] / dia
                    crear_directorio(destino)
                    with directorio(destino) as reclamada:
                        # El proceso único reclama por rename y recupera reclamos previos al reiniciar.
                        if (destino / nombre).exists():
                            continue
                        os.rename(nombre, nombre, src_dir_fd=carpeta, dst_dir_fd=reclamada)
                        os.fsync(reclamada)
                        os.fsync(carpeta)
                        cuarentena = legacy.parent / (legacy.name + "-rechazados")
                        procesar_archivo(conexion, config, origen, dia, nombre, reclamada, cuarentena, resultado, False)
                except OSError:
                    registrar_error(resultado, f"No se pudo procesar {origen[0]}/{origen[2]}/{dia}/{nombre}.")
    except OSError:
        registrar_error(resultado, f"No se pudo revisar {origen[0]}/{origen[2]}/{dia}.")
    return procesadas


def importar(config):
    resultado = {"nuevas": 0, "rechazadas": 0, "errores": 0, "detalle": ""}
    with conectar(config) as conexion:
        if not conexion.execute("SELECT pg_try_advisory_lock(431006312) AS adquirido").fetchone()["adquirido"]:
            return None
        camaras = conexion.execute(
            """SELECT c.vivero_id, c.nodo_id, c.camara_id AS id, n.entrada_legacy FROM camaras_origen c
            JOIN nodos n ON n.vivero_id = c.vivero_id AND n.id = c.nodo_id
            ORDER BY c.vivero_id, c.nodo_id, c.camara_id""",
        ).fetchall()
        for fila in camaras:
            vivero, nodo, camara = (fila[k] for k in ("vivero_id", "nodo_id", "id"))
            origen = (vivero, nodo, camara)
            fuentes = [(config.archivo / "bandeja" / vivero / nodo, None, True)]
            if fila["entrada_legacy"]:
                entrada = Path(fila["entrada_legacy"])
                reclamada = entrada.parent / f".mgv-{vivero}-{nodo}-procesando"
                fuentes.extend(((reclamada, None, False), (entrada, reclamada, False)))
            restantes = TOPE_POR_CAMARA
            for raiz, reclamo, http in fuentes:
                if restantes <= 0:
                    break
                try:
                    restantes -= revisar_camara(conexion, config, origen, raiz, resultado, restantes, reclamo, http)
                except FileNotFoundError:
                    continue
                except OSError:
                    registrar_error(resultado, f"Entrada inaccesible para {vivero}/{camara}.")
        resultado["rechazadas"] = conexion.execute(
            "SELECT count(*) AS cantidad FROM origenes WHERE problema IS NOT NULL"
        ).fetchone()["cantidad"]
        conexion.execute(
            """
            INSERT INTO estado_importador (id, terminado_en, nuevas, rechazadas, errores, detalle)
            VALUES (true, now(), %(nuevas)s, %(rechazadas)s, %(errores)s, %(detalle)s)
            ON CONFLICT (id) DO UPDATE SET terminado_en = EXCLUDED.terminado_en,
              nuevas = EXCLUDED.nuevas, rechazadas = EXCLUDED.rechazadas,
              errores = EXCLUDED.errores, detalle = EXCLUDED.detalle
            """,
            resultado,
        )
    return resultado
