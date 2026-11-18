from datetime import UTC, datetime, time, timedelta

from .dominio import en_horario, turnos, zona

ATRASO_CONTACTO = timedelta(minutes=10)
ATRASO_ALTA = timedelta(seconds=90)
ATRASO_ENVIO = timedelta(hours=1)
ATRASO_IMPORTADOR = timedelta(minutes=5)


def viveros_visibles(conexion, usuario):
    if usuario["administrador"]:
        return conexion.execute("SELECT * FROM viveros ORDER BY nombre, id").fetchall()
    return conexion.execute(
        """SELECT v.* FROM viveros v JOIN permisos p ON p.vivero_id = v.id
        WHERE p.usuario_id = %s ORDER BY v.nombre, v.id""", (usuario["id"],),
    ).fetchall()


def alta_disponible(nodo, ahora):
    return bool(nodo["enlazado"] and nodo["alta_en"] and ahora - nodo["alta_en"] <= ATRASO_ALTA)


def captura_disponible(nodo, ahora):
    return alta_disponible(nodo, ahora) and "capturar" in nodo.get("reporte", {}).get("capacidades", [])


def ficha_camara(camara, sitio_zona, ahora):
    ultima = camara["capturada_en"]
    if not camara["activa"]:
        return "pausada", "En mantenimiento" if ultima else "Sin habilitar"
    if ultima and ultima > ahora + timedelta(minutes=5):
        return "atrasada", "Revisar reloj"
    tolerancia = timedelta(minutes=max(5, camara["atraso_minutos"] - camara["intervalo"]))
    esperado = next(turnos(
        ahora - tolerancia, sitio_zona, camara["intervalo"], camara["desde"], camara["hasta"],
    ), None)
    if esperado and esperado > camara["actualizada_en"] and (not ultima or ultima < esperado):
        return "atrasada", "Captura atrasada"
    if not en_horario(ahora.astimezone(sitio_zona).time(), camara["desde"], camara["hasta"]):
        return "programada", "Fuera de horario"
    if not ultima:
        return "esperando", "Esperando JPG"
    return "aldia", "Al día"


def resumen_camaras(conexion, viveros, ahora=None):
    ahora = ahora or datetime.now(UTC)
    ids = [v["id"] for v in viveros]
    camaras = conexion.execute(
        """SELECT c.*, foto.id AS foto_id, foto.capturada_en, foto.recibida_en, foto.bytes,
            coalesce(hoy.cantidad, 0) AS recibidas
        FROM camaras c JOIN viveros v ON c.vivero_id = v.id
        LEFT JOIN LATERAL (
            SELECT id, capturada_en, recibida_en, bytes FROM capturas
            WHERE vivero_id = c.vivero_id AND camara_id = c.id
            ORDER BY capturada_en DESC LIMIT 1
        ) foto ON true
        LEFT JOIN LATERAL (
            SELECT count(*) AS cantidad FROM capturas
            WHERE vivero_id = c.vivero_id AND camara_id = c.id
            AND capturada_en >= (date_trunc('day', %s::timestamptz AT TIME ZONE v.zona) AT TIME ZONE v.zona)
            AND capturada_en <= %s::timestamptz + interval '5 minutes'
        ) hoy ON true
        WHERE c.vivero_id = ANY(%s)
        ORDER BY c.hilera, c.id""",
        (ahora, ahora, ids),
    ).fetchall()
    sitios = {v["id"]: {**v, "camaras": [], "nodos": []} for v in viveros}
    for camara in camaras:
        vivero = sitios[camara["vivero_id"]]
        sitio_zona = zona(vivero["zona"])
        estado, etiqueta = ficha_camara(camara, sitio_zona, ahora)
        medianoche = datetime.combine(ahora.astimezone(sitio_zona).date(), time.min, sitio_zona).astimezone(UTC)
        inicio = max(medianoche, camara["actualizada_en"])
        esperadas = sum(1 for _ in turnos(
            ahora, sitio_zona, camara["intervalo"], camara["desde"], camara["hasta"],
            ahora - inicio + timedelta(microseconds=1),
        )) if camara["activa"] else 0
        vivero["camaras"].append({**camara, "estado": estado, "etiqueta": etiqueta, "esperadas": esperadas})
    nodos = conexion.execute(
        """SELECT vivero_id, id, nombre, plano_x, plano_y, ultimo_contacto, autorizado_en, alta_en, reporte,
           token_sha IS NOT NULL AS enlazado, entrada_legacy IS NOT NULL AS tiene_entrada
        FROM nodos WHERE vivero_id = ANY(%s) ORDER BY id""", (ids,),
    ).fetchall()
    for nodo in nodos:
        sitios[nodo["vivero_id"]]["nodos"].append(nodo)
    for vivero in sitios.values():
        fichas = vivero["camaras"]
        vivero["aldia"] = sum(c["estado"] == "aldia" for c in fichas)
        vivero["activas"] = sum(c["activa"] for c in fichas)
        vivero["atrasadas"] = sum(c["estado"] == "atrasada" for c in fichas)
        vivero["recibidas"] = sum(c["recibidas"] for c in fichas)
        vivero["esperadas"] = sum(c["esperadas"] for c in fichas)
        vivero["ultima"] = max((c["capturada_en"] for c in fichas if c["capturada_en"]), default=None)
    return list(sitios.values())

