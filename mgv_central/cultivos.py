import csv
from datetime import timedelta
from io import StringIO

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from psycopg.types.json import Jsonb
from starlette.datastructures import FormData

from .acceso import exigir
from .base import auditar, conectar
from .configuracion import cargar_configuracion
from .dominio import SERIES, escala, fecha_formulario, guardar_sensores, rango_local, region, texto, zona
from .web import csv_respuesta, datos_formulario, fila, foto_ruta, local_hoy, pagina, puesto, sitio, volver

rutas = APIRouter()


def lote_actual(conexion, lote):
    return fila(conexion, "SELECT * FROM lotes WHERE id = %s", (lote,), "Lote desconocido.")


def formulario_lote(conexion, peticion, vivero, registro=None, camara=None):
    usuario = exigir(conexion, peticion, vivero, editar=True)
    sitio_actual = sitio(conexion, vivero)
    opciones = conexion.execute(
        """SELECT c.*, f.id AS foto_id, f.ancho AS foto_ancho, f.alto AS foto_alto
        FROM camaras c LEFT JOIN LATERAL (
            SELECT id, ancho, alto FROM capturas WHERE vivero_id = c.vivero_id AND camara_id = c.id
            ORDER BY capturada_en DESC LIMIT 1
        ) f ON true WHERE c.vivero_id = %s ORDER BY c.hilera, c.id""", (vivero,),
    ).fetchall()
    if not opciones:
        raise HTTPException(409, "Agregá una cámara al invernáculo antes de crear un lote.")
    seleccion = registro["camara_id"] if registro else camara
    elegida = next((c for c in opciones if c["id"] == seleccion), opciones[0])
    return pagina(peticion, "lote_form.html", usuario, sitio=sitio_actual, lote=registro, camara=elegida,
                  opciones=opciones, hoy=local_hoy(sitio_actual["zona"]), titulo="Editar lote" if registro else "Nuevo lote")


def datos_lote(datos):
    siembra = fecha_formulario(datos.get("siembra", ""))
    cosecha = fecha_formulario(datos["cosecha"]) if datos.get("cosecha") else None
    if cosecha and cosecha < siembra:
        raise ValueError("La cosecha debe ser posterior a la siembra.")
    retencion = int(datos["retencion"]) if datos.get("retencion") else None
    if retencion is not None and not 1 <= retencion <= 3650:
        raise ValueError("Ingresá de 1 a 3650 días de retención.")
    referencia = escala(datos)
    return (texto(datos.get("nombre", ""), 80, True), texto(datos.get("especie", ""), 60, True),
            texto(datos.get("variedad", ""), 60), texto(datos.get("bandejas", ""), 120), siembra,
            cosecha, Jsonb(region(datos)), Jsonb(referencia) if referencia else None, retencion)


def encolar_lote(conexion, usuario, vivero, lote, revision):
    conexion.execute(
        """INSERT INTO trabajos (vivero_id, usuario_id, tipo, clave, datos)
        VALUES (%s, %s, 'reanalizar', %s, %s)
        ON CONFLICT (clave) DO UPDATE SET estado = 'pendiente', intentos = 0, disponible_en = now(), mensaje = ''
        WHERE trabajos.estado IN ('listo', 'error')""",
        (vivero, usuario["id"], f"lote:{lote}:{revision}", Jsonb({"lote": lote})),
    )


@rutas.get("/viveros/{vivero}/lotes")
def lotes(peticion: Request, vivero: str):
    with conectar(cargar_configuracion()) as conexion:
        usuario = exigir(conexion, peticion, vivero)
        registro = sitio(conexion, vivero)
        datos = conexion.execute(
            """SELECT l.*, c.nombre AS camara FROM lotes l JOIN camaras c
            ON c.vivero_id = l.vivero_id AND c.id = l.camara_id
            WHERE l.vivero_id = %s ORDER BY l.cosecha IS NOT NULL, l.siembra DESC, l.id DESC""",
            (vivero,),
        ).fetchall()
    return pagina(peticion, "lotes.html", usuario, sitio=registro, lotes=datos, titulo="Lotes")


@rutas.get("/viveros/{vivero}/lotes/nuevo")
def nuevo_lote(peticion: Request, vivero: str, camara: str | None = None):
    with conectar(cargar_configuracion()) as conexion:
        return formulario_lote(conexion, peticion, vivero, camara=camara)


@rutas.post("/viveros/{vivero}/lotes")
def guardar_lote(peticion: Request, vivero: str, datos: FormData = Depends(datos_formulario)):
    with conectar(cargar_configuracion()) as conexion:
        usuario = exigir(conexion, peticion, vivero, editar=True, formulario=datos)
        camara = puesto(conexion, vivero, datos.get("camara", ""))
        with conexion.transaction():
            lote = conexion.execute(
                """INSERT INTO lotes (vivero_id, camara_id, nombre, especie, variedad, bandejas, siembra,
                  cosecha, roi, escala, retencion_dias)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id, revision""",
                (vivero, camara["id"], *datos_lote(datos)),
            ).fetchone()
            encolar_lote(conexion, usuario, vivero, lote["id"], lote["revision"])
            auditar(conexion, usuario, vivero, "crear lote", str(lote["id"]))
    return volver(f"/lotes/{lote['id']}")


@rutas.get("/lotes/{lote}/editar")
def editar_lote(peticion: Request, lote: int):
    with conectar(cargar_configuracion()) as conexion:
        registro = lote_actual(conexion, lote)
        return formulario_lote(conexion, peticion, registro["vivero_id"], registro=registro)


@rutas.post("/lotes/{lote}/editar")
def actualizar_lote(peticion: Request, lote: int, datos: FormData = Depends(datos_formulario)):
    with conectar(cargar_configuracion()) as conexion:
        registro = lote_actual(conexion, lote)
        vivero = registro["vivero_id"]
        usuario = exigir(conexion, peticion, vivero, editar=True, formulario=datos)
        valores = datos_lote(datos)
        with conexion.transaction():
            cambio = conexion.execute(
                """UPDATE lotes SET nombre = %s, especie = %s, variedad = %s, bandejas = %s, siembra = %s,
                cosecha = %s, roi = %s, escala = %s, retencion_dias = %s, revision = revision +
                  CASE WHEN (roi, escala) IS DISTINCT FROM (%s::jsonb, %s::jsonb) THEN 1 ELSE 0 END
                WHERE id = %s RETURNING revision""", (*valores, valores[6], valores[7], lote),
            ).fetchone()
            if cambio["revision"] != registro["revision"] or valores[4:6] != (registro["siembra"], registro["cosecha"]):
                encolar_lote(conexion, usuario, vivero, lote, cambio["revision"])
            auditar(conexion, usuario, vivero, "editar lote", str(lote))
    return volver(f"/lotes/{lote}")


MEDICIONES = """
    FROM mediciones m JOIN capturas f ON f.id = m.captura_id
    JOIN lotes l ON l.id = m.lote_id JOIN viveros v ON v.id = l.vivero_id
    JOIN calidad q ON q.captura_id = f.id
    WHERE m.lote_id = %s AND m.revision = l.revision
    AND (f.capturada_en AT TIME ZONE v.zona)::date >= l.siembra
    AND (l.cosecha IS NULL OR (f.capturada_en AT TIME ZONE v.zona)::date <= l.cosecha)
    AND (q.revision = 'aceptada' OR (q.revision IS NULL AND cardinality(q.problemas) = 0))
"""


@rutas.get("/lotes/{lote}")
def ver_lote(peticion: Request, lote: int):
    with conectar(cargar_configuracion()) as conexion:
        registro = lote_actual(conexion, lote)
        usuario = exigir(conexion, peticion, registro["vivero_id"])
        sitio_actual = sitio(conexion, registro["vivero_id"])
        dias = conexion.execute(
            """SELECT (f.capturada_en AT TIME ZONE v.zona)::date AS dia,
                avg(m.cobertura) AS cobertura, avg(m.area_cm2) AS area_cm2, count(*) AS muestras """
            + MEDICIONES + " GROUP BY dia ORDER BY dia", (lote,),
        ).fetchall()
        ultima = conexion.execute(
            "SELECT m.*, f.capturada_en " + MEDICIONES + " ORDER BY f.capturada_en DESC LIMIT 1", (lote,),
        ).fetchone()
        notas = conexion.execute(
            """SELECT n.*, u.nombre FROM notas_lote n JOIN usuarios u ON u.id = n.usuario_id
            WHERE n.lote_id = %s ORDER BY n.id DESC LIMIT 100""", (lote,),
        ).fetchall()
        sensores = conexion.execute(
            """SELECT avg(temp_c) AS temp_c, avg(humedad_pct) AS humedad_pct, avg(lux) AS lux, avg(co2_ppm) AS co2_ppm,
               count(*) AS muestras FROM sensores s JOIN viveros v ON v.id = s.vivero_id
               WHERE s.vivero_id = %s AND (s.fecha AT TIME ZONE v.zona)::date BETWEEN %s AND %s""",
            (registro["vivero_id"], registro["siembra"], registro["cosecha"] or local_hoy(sitio_actual["zona"])),
        ).fetchone()
    puntos = [[fila_dia["dia"].isoformat(), round(fila_dia["cobertura"], 2)] for fila_dia in dias]
    vence = registro["cosecha"] + timedelta(days=registro["retencion_dias"]) if registro["cosecha"] and registro["retencion_dias"] else None
    return pagina(peticion, "lote.html", usuario, lote=registro, sitio=sitio_actual, notas=notas,
                  dias=dias, ultima=ultima, puntos=puntos, vence=vence, sensores=sensores, series=SERIES,
                  titulo=registro["nombre"])


@rutas.get("/lotes/{lote}/mediciones.csv")
def csv_lote(peticion: Request, lote: int):
    with conectar(cargar_configuracion()) as conexion:
        registro = lote_actual(conexion, lote)
        exigir(conexion, peticion, registro["vivero_id"])
        mediciones = conexion.execute(
            "SELECT f.capturada_en, m.cobertura, m.area_cm2, m.algoritmo, m.revision " + MEDICIONES +
            " ORDER BY f.capturada_en", (lote,),
        ).fetchall()
    return csv_respuesta(f"lote-{lote}", ("captura_utc", "cobertura_pct", "area_cm2", "algoritmo", "revision_roi"),
                         ((m["capturada_en"].isoformat(), m["cobertura"], m["area_cm2"], m["algoritmo"],
                           m["revision"]) for m in mediciones))


@rutas.get("/lotes/{lote}/mascaras/{captura}.png")
def mascara(peticion: Request, lote: int, captura: int):
    config = cargar_configuracion()
    with conectar(config) as conexion:
        registro = lote_actual(conexion, lote)
        exigir(conexion, peticion, registro["vivero_id"])
        resultado = fila(conexion, """SELECT mascara FROM mediciones WHERE lote_id = %s
            AND captura_id = %s AND revision = %s""", (lote, captura, registro["revision"]), "Medición desconocida.")
    return FileResponse(foto_ruta(config, "mascaras", resultado["mascara"]), media_type="image/png")


@rutas.post("/lotes/{lote}/notas")
def nota(peticion: Request, lote: int, datos: FormData = Depends(datos_formulario)):
    with conectar(cargar_configuracion()) as conexion:
        registro = lote_actual(conexion, lote)
        usuario = exigir(conexion, peticion, registro["vivero_id"], editar=True, formulario=datos)
        tipo = datos.get("tipo")
        if tipo not in ("riego", "luz", "observacion", "cosecha"):
            raise ValueError("Tipo de nota desconocido.")
        conexion.execute(
            "INSERT INTO notas_lote (lote_id, usuario_id, tipo, texto) VALUES (%s, %s, %s, %s)",
            (lote, usuario["id"], tipo, texto(datos.get("texto", ""), 1200, True)),
        )
    return volver(f"/lotes/{lote}")


@rutas.post("/lotes/{lote}/analizar")
def analizar(peticion: Request, lote: int, datos: FormData = Depends(datos_formulario)):
    with conectar(cargar_configuracion()) as conexion:
        registro = lote_actual(conexion, lote)
        usuario = exigir(conexion, peticion, registro["vivero_id"], editar=True, formulario=datos)
        encolar_lote(conexion, usuario, registro["vivero_id"], lote, registro["revision"])
    return volver("/trabajos")


@rutas.get("/viveros/{vivero}/sensores")
def sensores(peticion: Request, vivero: str, desde: str = "", hasta: str = ""):
    with conectar(cargar_configuracion()) as conexion:
        usuario = exigir(conexion, peticion, vivero)
        sitio_actual = sitio(conexion, vivero)
        hoy = local_hoy(sitio_actual["zona"])
        desde, hasta = desde or str(hoy - timedelta(days=6)), hasta or str(hoy)
        inicial, final = rango_local(desde, hasta, zona(sitio_actual["zona"]), 90)
        lecturas = conexion.execute(
            """SELECT date_trunc('hour', fecha) AS hora, avg(temp_c) AS temp_c, avg(humedad_pct) AS humedad_pct,
               avg(lux) AS lux, avg(co2_ppm) AS co2_ppm FROM sensores
            WHERE vivero_id = %s AND fecha >= %s AND fecha < %s GROUP BY hora ORDER BY hora""",
            (vivero, inicial, final),
        ).fetchall()
    series = {
        nombre: [[f["hora"].astimezone(zona(sitio_actual["zona"])).isoformat()[:16], round(f[campo], 2)]
                 for f in lecturas if f[campo] is not None] for campo, nombre in SERIES.items()
    }
    return pagina(peticion, "sensores.html", usuario, sitio=sitio_actual, desde=desde, hasta=hasta,
                  series=series, titulo="Sensores")


@rutas.post("/viveros/{vivero}/sensores")
def importar_sensores(peticion: Request, vivero: str, datos: FormData = Depends(datos_formulario)):
    with conectar(cargar_configuracion()) as conexion:
        usuario = exigir(conexion, peticion, vivero, editar=True, formulario=datos)
        contenido = datos.get("csv", "").strip().lstrip("\ufeff")
        encabezado = contenido.split("\n", 1)[0]
        separador = ";" if ";" in encabezado else ","
        registros = list(csv.DictReader(StringIO(contenido), delimiter=separador))
        if len(registros) > 5000:
            raise ValueError("Dividí la importación en archivos de hasta 5000 lecturas.")
        with conexion.transaction():
            cantidad = guardar_sensores(conexion, vivero, "importado", registros)
            auditar(conexion, usuario, vivero, "importar sensores", str(cantidad))
    return volver(f"/viveros/{vivero}/sensores")
