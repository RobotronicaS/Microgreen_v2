from datetime import datetime
from io import BytesIO
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from time import monotonic
from zipfile import ZIP_STORED, ZipFile

import numpy as np
from PIL import Image, ImageDraw, ImageOps

from .base import conectar
from .ingesta import guardar_archivo

ALGORITMO = "rgb-exceso-verde-1"
ANALISIS = ("analisis", "reanalizar")
EXPORTACIONES = ("timelapse", "zip")


class TrabajoInvalido(ValueError):
    pass


def evaluar_foto(imagen, anterior, huella):
    miniatura = imagen.copy()
    miniatura.thumbnail((512, 512))
    gris = np.asarray(miniatura.convert("L"), dtype=np.float32)
    brillo = float(np.median(gris))
    nitidez = 0.0
    if min(gris.shape) >= 3:
        centro = gris[1:-1, 1:-1]
        laplace = gris[:-2, 1:-1] + gris[2:, 1:-1] + gris[1:-1, :-2] + gris[1:-1, 2:] - 4 * centro
        nitidez = float(laplace.var())
    firma = np.asarray(miniatura.convert("L").resize((20, 20), Image.Resampling.BILINEAR), dtype=np.uint8)
    problemas = []
    if brillo < 27:
        problemas.append("Oscura")
    if nitidez < 14:
        problemas.append("Nitidez baja")
    if anterior and anterior["sha256"] == huella:
        problemas.append("JPG repetido")
    if anterior and anterior["firma"] and brillo >= 27 and anterior["brillo"] >= 27:
        previa = np.frombuffer(bytes.fromhex(anterior["firma"]), dtype=np.uint8).reshape(20, 20).astype(float)
        actual = firma.astype(float)
        borde = np.ones((20, 20), dtype=bool)
        borde[4:-4, 4:-4] = False
        a, b = actual[borde], previa[borde]
        if min(a.std(), b.std()) > 15:
            diferencia = np.mean(np.abs((a - a.mean()) / a.std() - (b - b.mean()) / b.std()))
            if diferencia > 0.9:
                problemas.append("Posible cambio de encuadre")
    return problemas, brillo, nitidez, firma.tobytes().hex()


def medir_foto(imagen, region, escala):
    ancho, alto = imagen.size
    x, y, w, h = region
    a = max(0, min(ancho - 1, round(ancho * x)))
    b = max(0, min(alto - 1, round(alto * y)))
    c = max(a + 1, min(ancho, round(ancho * (x + w))))
    d = max(b + 1, min(alto, round(alto * (y + h))))
    recorte = imagen.crop((a, b, c, d))
    recorte.thumbnail((640, 640))
    rojo, verde, azul = np.moveaxis(np.asarray(recorte, dtype=np.float32), -1, 0)
    pixeles = (2 * verde - rojo - azul > 24) & (verde > 1.08 * rojo) & (verde > 1.06 * azul) & (verde >= 35)
    cobertura = float(pixeles.mean() * 100)
    area = None
    if escala:
        cm, px, ancho_ref, alto_ref = escala
        factor = ancho / ancho_ref
        if abs(factor - alto / alto_ref) <= factor * 0.02:
            area = cobertura / 100 * (c - a) * (d - b) * (cm / (px * factor)) ** 2
    vista = imagen.copy()
    vista.thumbnail((860, 620))
    av, bv = round(a * vista.width / ancho), round(b * vista.height / alto)
    cv = max(av + 1, round(c * vista.width / ancho))
    dv = max(bv + 1, round(d * vista.height / alto))
    mascara = Image.fromarray(pixeles.astype(np.uint8) * 130).resize((cv - av, dv - bv), Image.Resampling.NEAREST)
    vista.paste((92, 242, 109), (av, bv, cv, dv), mascara)
    ImageDraw.Draw(vista).rectangle((av, bv, cv - 1, dv - 1), outline=(255, 230, 135), width=2)
    salida = BytesIO()
    vista.save(salida, "PNG", optimize=False)
    return cobertura, area, salida.getvalue()


def jpg_original(config, clave):
    ruta = (config.archivo / "originales" / clave).resolve()
    if not ruta.is_relative_to((config.archivo / "originales").resolve()) or not ruta.is_file():
        raise TrabajoInvalido("Falta un JPG original. Revisá el archivo y su respaldo.")
    return ruta


def analizar(conexion, config, trabajo):
    foto = conexion.execute(
        """SELECT f.*, v.zona FROM capturas f JOIN viveros v ON v.id = f.vivero_id
        WHERE f.id = %s AND f.vivero_id = %s""", (trabajo["datos"]["captura"], trabajo["vivero_id"]),
    ).fetchone()
    if foto is None:
        raise TrabajoInvalido("La captura no está en el catálogo.")
    anterior = conexion.execute(
        """SELECT f.sha256, q.firma, q.brillo FROM capturas f
        LEFT JOIN calidad q ON q.captura_id = f.id
        WHERE f.vivero_id = %s AND f.camara_id = %s AND f.capturada_en < %s
        AND f.capturada_en >= %s::timestamptz - interval '4 hours'
        ORDER BY f.capturada_en DESC LIMIT 1""",
        (foto["vivero_id"], foto["camara_id"], foto["capturada_en"], foto["capturada_en"]),
    ).fetchone()
    with Image.open(jpg_original(config, foto["clave_archivo"])) as original:
        imagen = ImageOps.exif_transpose(original).convert("RGB")
    problemas, brillo, nitidez, firma = evaluar_foto(imagen, anterior, foto["sha256"])
    calidad = conexion.execute(
        """INSERT INTO calidad (captura_id, problemas, brillo, nitidez, firma) VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (captura_id) DO UPDATE SET problemas = EXCLUDED.problemas, brillo = EXCLUDED.brillo,
          nitidez = EXCLUDED.nitidez, firma = EXCLUDED.firma RETURNING revision""",
        (foto["id"], problemas, brillo, nitidez, firma),
    ).fetchone()
    if calidad["revision"] == "descartada" or (problemas and calidad["revision"] != "aceptada"):
        return "Captura marcada para revisión.", None
    lotes = conexion.execute(
        """SELECT * FROM lotes WHERE vivero_id = %s AND camara_id = %s
        AND siembra <= (%s::timestamptz AT TIME ZONE %s)::date
        AND (cosecha IS NULL OR cosecha >= (%s::timestamptz AT TIME ZONE %s)::date)""",
        (foto["vivero_id"], foto["camara_id"], foto["capturada_en"], foto["zona"],
         foto["capturada_en"], foto["zona"]),
    ).fetchall()
    for lote in lotes:
        cobertura, area, mascara = medir_foto(imagen, lote["roi"], lote["escala"])
        clave = f"{foto['vivero_id']}/{lote['id']}/r{lote['revision']}/{foto['id']}.png"
        guardar_archivo(config.archivo / "mascaras" / clave, mascara)
        conexion.execute(
            """INSERT INTO mediciones (captura_id, lote_id, revision, algoritmo, cobertura, area_cm2, mascara)
            VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (captura_id, lote_id, revision) DO UPDATE
              SET algoritmo = EXCLUDED.algoritmo, cobertura = EXCLUDED.cobertura, area_cm2 = EXCLUDED.area_cm2,
                  mascara = EXCLUDED.mascara, medida_en = now()""",
            (foto["id"], lote["id"], lote["revision"], ALGORITMO, cobertura, area, clave),
        )
    return f"Calidad evaluada · {len(lotes)} lotes medidos.", None


def reanalizar(conexion, config, trabajo):
    lote = trabajo["datos"]["lote"]
    cantidad = conexion.execute(
        """INSERT INTO trabajos (vivero_id, tipo, clave, datos)
        SELECT f.vivero_id, 'analisis', 'foto:' || f.id, jsonb_build_object('captura', f.id)
        FROM lotes l JOIN viveros v ON v.id = l.vivero_id
        JOIN capturas f ON f.vivero_id = l.vivero_id AND f.camara_id = l.camara_id
        WHERE l.id = %s AND l.vivero_id = %s AND f.capturada_en >= (l.siembra::timestamp AT TIME ZONE v.zona)
        AND (l.cosecha IS NULL OR f.capturada_en < ((l.cosecha + 1)::timestamp AT TIME ZONE v.zona))
        ORDER BY f.capturada_en
        ON CONFLICT (clave) DO UPDATE SET estado = 'pendiente', disponible_en = now(), intentos = 0
        WHERE trabajos.estado IN ('listo', 'error')""",
        (lote, trabajo["vivero_id"]),
    ).rowcount
    return f"{cantidad} capturas programadas para análisis.", None


def timelapse(conexion, config, trabajo, temporal):
    datos = trabajo["datos"]
    registros = conexion.execute(
        """SELECT f.clave_archivo FROM capturas f JOIN calidad q ON q.captura_id = f.id
        WHERE f.vivero_id = %s AND f.camara_id = %s AND f.capturada_en >= %s AND f.capturada_en < %s
        AND (q.revision = 'aceptada' OR (q.revision IS NULL AND cardinality(q.problemas) = 0))
        ORDER BY f.capturada_en""",
        (trabajo["vivero_id"], datos["camara"], datetime.fromisoformat(datos["desde"]),
         datetime.fromisoformat(datos["hasta"])),
    ).fetchall()
    if not registros:
        raise TrabajoInvalido("No hay fotos aptas en ese rango. Revisá la calidad o elegí otras fechas.")
    if len(registros) > 1500:
        registros = [registros[round(i * (len(registros) - 1) / 1499)] for i in range(1500)]
    for numero, foto in enumerate(registros, 1):
        with Image.open(jpg_original(config, foto["clave_archivo"])) as original:
            cuadro = ImageOps.pad(ImageOps.exif_transpose(original).convert("RGB"), (960, 540))
            cuadro.save(temporal / f"{numero:05d}.jpg", "JPEG", quality=85)
    destino = temporal / "resultado.mp4"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-framerate", str(datos["fps"]),
         "-i", str(temporal / "%05d.jpg"), "-an", "-c:v", "libx264", "-threads", "2",
         "-preset", "veryfast", "-crf", "24", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(destino)],
        check=True, timeout=600, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return f"Video de {len(registros)} cuadros.", destino


def zip_originales(conexion, config, trabajo, temporal):
    datos = trabajo["datos"]
    registros = conexion.execute(
        """SELECT clave_archivo FROM capturas WHERE vivero_id = %s AND camara_id = %s AND id = ANY(%s)
        ORDER BY capturada_en""", (trabajo["vivero_id"], datos["camara"], datos["fotos"]),
    ).fetchall()
    if len(registros) != len(set(datos["fotos"])):
        raise TrabajoInvalido("Alguna foto ya no está disponible.")
    destino = temporal / "resultado.zip"
    with ZipFile(destino, "w", compression=ZIP_STORED) as salida:
        for registro in registros:
            jpg = jpg_original(config, registro["clave_archivo"])
            salida.write(jpg, f"{datos['camara']}/{jpg.name}")
    return f"{len(registros)} originales.", destino


def exportar(conexion, config, trabajo):
    if shutil.disk_usage(config.archivo).free < config.min_libre_gib * 1024**3:
        raise TrabajoInvalido("Liberá espacio en el archivo central antes de generar este archivo.")
    carpeta = config.archivo / "trabajos"
    carpeta.mkdir(exist_ok=True, mode=0o700)
    with TemporaryDirectory(dir=carpeta, prefix=".generando-") as temporal:
        funcion = timelapse if trabajo["tipo"] == "timelapse" else zip_originales
        detalle, archivo = funcion(conexion, config, trabajo, Path(temporal))
        nombre = f"{trabajo['tipo']}-{trabajo['id']}{archivo.suffix}"
        archivo.replace(carpeta / nombre)
    return detalle, nombre


def siguiente_trabajo(conexion, tipos):
    return conexion.execute(
        """UPDATE trabajos SET estado = 'en_proceso', inicio_en = now(), intentos = intentos + 1
        WHERE id = (SELECT id FROM trabajos WHERE estado = 'pendiente' AND tipo = ANY(%s)
          AND disponible_en <= now() ORDER BY (tipo = 'reanalizar') DESC, disponible_en, id
          FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *""",
        (list(tipos),),
    ).fetchone()


def procesar(config, media=False):
    tipos, candado = (EXPORTACIONES, 431006316) if media else (ANALISIS, 431006315)
    with conectar(config) as conexion:
        if not conexion.execute("SELECT pg_try_advisory_lock(%s) AS adquirido", (candado,)).fetchone()["adquirido"]:
            return 0
        conexion.execute(
            """UPDATE trabajos SET estado = 'pendiente', disponible_en = now()
            WHERE estado = 'en_proceso' AND tipo = ANY(%s)""", (list(tipos),),
        )
        inicio = monotonic()
        cantidad = 0
        funciones = {"analisis": analizar, "reanalizar": reanalizar, "timelapse": exportar, "zip": exportar}
        while cantidad < (1 if media else 90) and monotonic() - inicio < 30:
            trabajo = siguiente_trabajo(conexion, tipos)
            if not trabajo:
                break
            try:
                mensaje, salida = funciones[trabajo["tipo"]](conexion, config, trabajo)
                conexion.execute(
                    """UPDATE trabajos SET estado = 'listo', fin_en = now(), mensaje = %s, salida = %s
                    WHERE id = %s""", (mensaje, salida, trabajo["id"]),
                )
            except (TrabajoInvalido, OSError, subprocess.SubprocessError) as error:
                terminal = isinstance(error, TrabajoInvalido) or trabajo["intentos"] >= 3
                mensaje = str(error) if isinstance(error, TrabajoInvalido) else "Falló el acceso a un archivo o FFmpeg."
                conexion.execute(
                    """UPDATE trabajos SET estado = %s, mensaje = %s, fin_en = now(),
                      disponible_en = now() + interval '1 minute' WHERE id = %s""",
                    ("error" if terminal else "pendiente", mensaje, trabajo["id"]),
                )
            cantidad += 1
    return cantidad
