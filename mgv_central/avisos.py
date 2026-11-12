from datetime import UTC, datetime
from email.message import EmailMessage
import json
import shutil
import smtplib
import ssl
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .altas import limpiar_altas
from .base import conectar
from .configuracion import ruta_configuracion
from .resumen import ATRASO_CONTACTO, ATRASO_ENVIO, ATRASO_IMPORTADOR, resumen_camaras


def opciones_avisos():
    ruta = ruta_configuracion().with_name("avisos.json")
    if not ruta.exists():
        return {}
    contenido = json.loads(ruta.read_text(encoding="utf-8"))
    return {canal: contenido[canal] for canal in ("correo", "telegram")
            if isinstance(contenido.get(canal), dict) and contenido[canal].get("habilitado") is True}


def incidentes(conexion, config):
    ahora = datetime.now(UTC)
    sitios = resumen_camaras(conexion, conexion.execute("SELECT * FROM viveros").fetchall(), ahora)
    hallazgos = {}
    for sitio in sitios:
        vivero = sitio["id"]
        for camara in sitio["camaras"]:
            if camara["estado"] == "atrasada":
                hallazgos[f"camara:{vivero}:{camara['id']}"] = (
                    vivero, "aviso", f"{sitio['nombre']} · {camara['nombre']} · {camara['etiqueta']}",
                )
        for nodo in sitio["nodos"]:
            clave = f"nodo:{vivero}:{nodo['id']}"
            reporte = nodo["reporte"]
            contacto = nodo["ultimo_contacto"] or nodo["autorizado_en"]
            if nodo["enlazado"] and contacto and ahora - contacto > ATRASO_CONTACTO:
                hallazgos[clave] = (vivero, "critica", f"{sitio['nombre']} · {nodo['nombre']} lleva más de 10 min sin reportar.")
            if reporte.get("libres_bytes", float("inf")) < config.min_libre_gib * 1024**3:
                hallazgos[f"{clave}:disco"] = (vivero, "critica", f"{sitio['nombre']} · poco espacio libre en {nodo['nombre']}.")
            if reporte.get("mas_antigua") and ahora - datetime.fromisoformat(reporte["mas_antigua"]) > ATRASO_ENVIO:
                hallazgos[f"{clave}:cola"] = (vivero, "aviso", f"{sitio['nombre']} · {nodo['nombre']} tiene JPG en cola por más de una hora.")
    if shutil.disk_usage(config.archivo).free < config.min_libre_gib * 1024**3:
        hallazgos["central:disco"] = (None, "critica", "El disco de Central está cerca de llenarse.")
    importador = conexion.execute("SELECT * FROM estado_importador WHERE id").fetchone()
    if not importador or ahora - importador["terminado_en"] > ATRASO_IMPORTADOR or importador["errores"]:
        hallazgos["central:importador"] = (None, "aviso", "Revisá el temporizador o los errores de recepción de Central.")
    for fila in conexion.execute(
        """SELECT vivero_id, count(*) AS cantidad FROM trabajos WHERE estado = 'error'
        AND tipo = 'analisis' GROUP BY vivero_id""",
    ):
        hallazgos[f"trabajos:{fila['vivero_id']}"] = (
            fila["vivero_id"], "aviso", f"{fila['cantidad']} análisis requieren revisión.",
        )
    return hallazgos


def actualizar_alertas(conexion, config, canales):
    hallazgos = incidentes(conexion, config)
    with conexion.transaction():
        for clave, (vivero, nivel, detalle) in hallazgos.items():
            fila = conexion.execute(
                """INSERT INTO alertas (clave, vivero_id, nivel, detalle) VALUES (%s, %s, %s, %s)
                ON CONFLICT (clave) WHERE cerrada_en IS NULL DO NOTHING RETURNING id""",
                (clave, vivero, nivel, detalle),
            ).fetchone()
            if fila:
                encolar_avisos(conexion, fila["id"], canales, False)
        cerradas = conexion.execute(
            """UPDATE alertas SET cerrada_en = now() WHERE cerrada_en IS NULL
            AND NOT (clave = ANY(%s)) RETURNING id""", (list(hallazgos),),
        ).fetchall()
        for fila in cerradas:
            encolar_avisos(conexion, fila["id"], canales, True)


def encolar_avisos(conexion, alerta, canales, recuperada):
    for canal in canales:
        conexion.execute(
            "INSERT INTO avisos (alerta_id, canal, recuperada) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (alerta, canal, recuperada),
        )


def enviar_correo(opciones, asunto, texto):
    mensaje = EmailMessage()
    mensaje["Subject"] = asunto
    mensaje["From"] = opciones["desde"]
    mensaje["To"] = ", ".join(opciones["para"])
    mensaje.set_content(texto)
    tls = ssl.create_default_context()
    if opciones.get("modo") == "ssl":
        with smtplib.SMTP_SSL(opciones["servidor"], opciones.get("puerto", 465), context=tls, timeout=8) as smtp:
            smtp.login(opciones["usuario"], opciones["clave"])
            smtp.send_message(mensaje)
    else:
        with smtplib.SMTP(opciones["servidor"], opciones.get("puerto", 587), timeout=8) as smtp:
            smtp.starttls(context=tls)
            smtp.login(opciones["usuario"], opciones["clave"])
            smtp.send_message(mensaje)


def enviar_telegram(opciones, asunto, texto):
    token = opciones["token"]
    if not token.replace(":", "").replace("_", "").replace("-", "").isalnum() or ":" not in token:
        raise ValueError("Token de Telegram inválido.")
    peticion = Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=urlencode({"chat_id": opciones["chat_id"], "text": f"{asunto}\n{texto}"}).encode(),
    )
    with urlopen(peticion, timeout=8) as respuesta:
        if not json.load(respuesta).get("ok"):
            raise OSError("Telegram rechazó el aviso.")


def supervisar(config):
    try:
        canales = opciones_avisos()
    except (OSError, ValueError, TypeError):
        print("Revisá avisos.json. Se evaluarán las alertas en el panel.", flush=True)
        canales = {}
    with conectar(config) as conexion:
        if not conexion.execute("SELECT pg_try_advisory_lock(431006317) AS adquirido").fetchone()["adquirido"]:
            return
        limpiar_altas(conexion)
        actualizar_alertas(conexion, config, canales)
        avisos = conexion.execute(
            """SELECT a.detalle, v.* FROM avisos v JOIN alertas a ON a.id = v.alerta_id
            WHERE v.enviada_en IS NULL AND v.siguiente <= now() AND v.intentos < 5 AND v.canal = ANY(%s)
            ORDER BY v.siguiente, v.alerta_id LIMIT 4""", (list(canales),),
        ).fetchall()
        for aviso in avisos:
            clave = (aviso["alerta_id"], aviso["canal"], aviso["recuperada"])
            asunto = "MicroGreenVision · alerta cerrada" if aviso["recuperada"] else "MicroGreenVision · alerta"
            try:
                enviar = enviar_correo if aviso["canal"] == "correo" else enviar_telegram
                enviar(canales[aviso["canal"]], asunto, aviso["detalle"])
            except (OSError, ValueError, KeyError, TypeError):
                print(f"Falló el aviso {aviso['alerta_id']} por {aviso['canal']}.", flush=True)
                conexion.execute("""UPDATE avisos SET intentos = intentos + 1, siguiente = now() + interval '10 minutes'
                    WHERE alerta_id = %s AND canal = %s AND recuperada = %s""", clave)
            else:
                conexion.execute("""UPDATE avisos SET enviada_en = now()
                    WHERE alerta_id = %s AND canal = %s AND recuperada = %s""", clave)
