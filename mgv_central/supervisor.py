"""Lecturas y estado operativo para el panel de supervisión."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
import os
import shutil

from .resumen import (
    ATRASO_CONTACTO, ATRASO_ENVIO, ATRASO_IMPORTADOR, resumen_camaras, viveros_visibles,
)
from .web import peso

GIB = 1024**3
SEVERIDAD = {"aviso": 1, "critico": 2}


def transcurrido(fecha, ahora):
    if fecha is None:
        return "Sin registro"
    segundos = max(0, int((ahora - fecha).total_seconds()))
    if segundos < 60:
        return f"Hace {segundos} s"
    if segundos < 3600:
        return f"Hace {segundos // 60} min"
    if segundos < 86400:
        horas, minutos = divmod(segundos // 60, 60)
        return f"Hace {horas} h {minutos} min"
    dias, horas = divmod(segundos // 3600, 24)
    return f"Hace {dias} d {horas} h"


def fecha_reporte(valor):
    if not isinstance(valor, str):
        return None
    try:
        fecha = datetime.fromisoformat(valor)
    except ValueError:
        return None
    return fecha if fecha.tzinfo else None


def estado_limite(valor, aviso, critico):
    if valor >= critico:
        return "critico"
    return "aviso" if valor >= aviso else "ok"


def estado_disco(libres, minimo):
    if libres < minimo:
        return "critico"
    return "aviso" if libres < 2 * minimo else "ok"


def indicador(nombre, valor, detalle, nivel="ok"):
    return {"nombre": nombre, "valor": valor, "detalle": detalle, "nivel": nivel}


def registrar_incidencia(incidencias, nivel, origen, mensaje, enlace):
    if nivel in SEVERIDAD:
        incidencias.append({"nivel": nivel, "origen": origen, "mensaje": mensaje, "enlace": enlace})


def memoria_linux():
    valores = {}
    for linea in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
        clave, _, valor = linea.partition(":")
        if clave in ("MemTotal", "MemAvailable"):
            valores[clave] = int(valor.split()[0]) * 1024
    total, disponible = valores["MemTotal"], valores["MemAvailable"]
    if total <= 0 or not 0 <= disponible <= total:
        raise ValueError("Lectura de memoria inválida.")
    return total, disponible


def recursos_central(config, minimo):
    try:
        carga = os.getloadavg()[1]
        nucleos = os.cpu_count()
        if not nucleos:
            raise ValueError("Cantidad de núcleos desconocida.")
        carga_estado = indicador(
            "Carga del servidor", f"{carga:.2f} / {nucleos}",
            "Carga media en 5 min / núcleos lógicos.", estado_limite(carga / nucleos, 1, 1.5),
        )
    except (AttributeError, OSError, ValueError):
        carga_estado = indicador("Carga del servidor", "Sin lectura", "Carga media en 5 min.", "sin_datos")

    try:
        total, disponible = memoria_linux()
        utilizado = (1 - disponible / total) * 100
        ram = indicador("RAM", f"{utilizado:.0f} %", f"{peso(disponible)} disponibles de {peso(total)}.",
                        estado_limite(utilizado, 85, 95))
    except (OSError, ValueError, KeyError, IndexError):
        ram = indicador("RAM", "Sin lectura", "Memoria del servidor.", "sin_datos")

    try:
        disco = shutil.disk_usage(config.archivo)
        espacio = indicador("Disco de imágenes", peso(disco.free),
                            f"Libres de {peso(disco.total)} · mínimo {peso(minimo)}.",
                            estado_disco(disco.free, minimo))
    except OSError:
        espacio = indicador("Disco de imágenes", "Inaccesible", "Revisá el almacenamiento de imágenes.", "critico")

    return [carga_estado, ram, espacio]


def estado_importador(fila, ahora):
    if not fila:
        return indicador("Recepción de JPG", "En espera", "Primera revisión pendiente.", "sin_datos")
    fecha = fila["terminado_en"]
    demora = ahora - fecha
    detalle = f"{fila['nuevas']} JPG en la última revisión."
    if demora > timedelta(minutes=10):
        return indicador("Recepción de JPG", transcurrido(fecha, ahora), "Revisá el servicio de importación.", "critico")
    if demora > ATRASO_IMPORTADOR:
        return indicador("Recepción de JPG", transcurrido(fecha, ahora), "La revisión de entrada está atrasada.", "aviso")
    if fila["errores"]:
        return indicador("Recepción de JPG", transcurrido(fecha, ahora),
                         f"{fila['errores']} errores en la última revisión.", "aviso")
    return indicador("Recepción de JPG", transcurrido(fecha, ahora), detalle)


def colas_central(conexion, ids, ahora, incidencias):
    if not ids:
        return []
    registros = conexion.execute(
        """SELECT CASE WHEN tipo IN ('analisis', 'reanalizar') THEN 'analisis' ELSE 'media' END AS grupo,
               count(*) FILTER (WHERE estado = 'pendiente') AS pendientes,
               count(*) FILTER (WHERE estado = 'en_proceso') AS en_curso,
               count(*) FILTER (WHERE estado = 'error') AS errores,
               min(disponible_en) FILTER (WHERE estado = 'pendiente' AND disponible_en <= %s) AS espera_desde,
               min(inicio_en) FILTER (WHERE estado = 'en_proceso') AS proceso_desde
        FROM trabajos
        WHERE vivero_id = ANY(%s) AND estado IN ('pendiente', 'en_proceso', 'error')
        GROUP BY 1""", (ahora, ids),
    ).fetchall()
    por_grupo = {fila["grupo"]: fila for fila in registros}
    colas = []
    for grupo, nombre, aviso, critico, maximo_proceso in (
        ("analisis", "Análisis de imágenes", 5, 15, 3),
        ("media", "ZIP y timelapse", 10, 30, 17),
    ):
        fila = por_grupo.get(grupo, {"pendientes": 0, "en_curso": 0, "errores": 0,
                                   "espera_desde": None, "proceso_desde": None})
        espera = fila["espera_desde"]
        inicio = fila["proceso_desde"]
        nivel_espera = estado_limite((ahora - espera).total_seconds() / 60, aviso, critico) if espera else "ok"
        registrar_incidencia(incidencias, nivel_espera, nombre, "La cola está demorada.", "/trabajos")
        if fila["errores"]:
            detalle = "1 trabajo con error." if fila["errores"] == 1 else f"{fila['errores']} trabajos con error."
            registrar_incidencia(incidencias, "aviso", nombre, detalle, "/trabajos")
        demorado = inicio and ahora - inicio > timedelta(minutes=maximo_proceso)
        if demorado:
            registrar_incidencia(incidencias, "aviso", nombre, "Hay un trabajo demorado en ejecución.", "/trabajos")
        nivel = nivel_espera
        if nivel == "ok" and (fila["errores"] or demorado):
            nivel = "aviso"
        colas.append({**fila, "nombre": nombre, "nivel": nivel,
                      "espera": transcurrido(espera, ahora) if espera else "—"})
    return colas


def diagnostico_nodo(nodo, camaras, sitio, ahora, minimo, incidencias):
    contexto = f"{sitio['nombre']} · {nodo['nombre']}"
    enlace = f"/viveros/{sitio['id']}"
    contacto = nodo["ultimo_contacto"]
    referencia = contacto or nodo["autorizado_en"]
    vigente = nodo["enlazado"] and contacto is not None and ahora - contacto <= ATRASO_CONTACTO
    if vigente:
        conexion = indicador("Conexión", "En contacto", transcurrido(contacto, ahora))
    elif nodo["enlazado"] and referencia and ahora - referencia > ATRASO_CONTACTO:
        conexion = indicador("Conexión", "Sin contacto", transcurrido(contacto, ahora), "critico")
        registrar_incidencia(incidencias, "critico", contexto, "Más de 10 min sin reporte del agente.", enlace)
    elif nodo["enlazado"]:
        conexion = indicador("Conexión", "Esperando reporte", "Primer contacto pendiente.", "sin_datos")
    elif nodo["tiene_entrada"]:
        conexion = indicador("Conexión", "Seguimiento por JPG", "Envío por rsync.", "sin_datos")
    else:
        conexion = indicador("Conexión", "Por vincular", "Nodo pendiente de enlazar.", "sin_datos")

    reporte = nodo["reporte"] if vigente else {}
    cola = reporte.get("pendientes")
    mas_antigua = fecha_reporte(reporte.get("mas_antigua")) if cola else None
    libres = reporte.get("libres_bytes")
    estado_libre = estado_disco(libres, minimo) if libres is not None else "sin_datos"
    registrar_incidencia(incidencias, estado_libre, contexto, f"Espacio disponible {peso(libres)}.", enlace)
    demora_cola = mas_antigua is not None and ahora - mas_antigua > ATRASO_ENVIO
    if demora_cola:
        registrar_incidencia(incidencias, "aviso", contexto, "Hay JPG pendientes de hace más de una hora.", enlace)
    if reporte.get("envio") == "error":
        registrar_incidencia(incidencias, "aviso", contexto, "La Zero informó un error de envío.", enlace)

    estado_camaras = reporte.get("camaras", {})
    fallas = [c["id"] for c in camaras if c["activa"] and c["estado"] != "programada"
              and estado_camaras.get(c["id"]) in ("error", "sin_configurar")]
    if fallas:
        detalle = "1 cámara requiere revisión." if len(fallas) == 1 else f"{len(fallas)} cámaras requieren revisión."
        registrar_incidencia(incidencias, "aviso", contexto, detalle, enlace)

    return {
        "nombre": nodo["nombre"], "conexion": conexion, "ultimo_reporte": contacto,
        "ultimo_jpg": max((c["capturada_en"] for c in camaras if c["capturada_en"]), default=None),
        "cola": cola, "edad_cola": transcurrido(mas_antigua, ahora) if mas_antigua else None,
        "demora_cola": demora_cola, "libres": libres, "estado_libre": estado_libre,
        "fallas": fallas, "error_envio": reporte.get("envio") == "error",
    }


def estado_supervisor(conexion, config, usuario, ahora=None):
    ahora = ahora or datetime.now(UTC)
    visibles = viveros_visibles(conexion, usuario)
    sitios = resumen_camaras(conexion, visibles, ahora) if visibles else []
    incidencias = []
    minimo = config.min_libre_gib * GIB
    servidor = []
    if usuario["administrador"]:
        servidor = recursos_central(config, minimo)
        fila = conexion.execute("SELECT * FROM estado_importador WHERE id").fetchone()
        servidor.append(estado_importador(fila, ahora))
        for recurso in servidor:
            mensaje = f"{recurso['valor']} · {recurso['detalle']}"
            registrar_incidencia(incidencias, recurso["nivel"], recurso["nombre"], mensaje, "#servidor")
    colas = colas_central(conexion, [v["id"] for v in sitios], ahora, incidencias)

    for sitio in sitios:
        primera_incidencia = len(incidencias)
        atrasadas = [c for c in sitio["camaras"] if c["estado"] == "atrasada"]
        sitio["camaras_atrasadas"] = atrasadas
        sitio["en_espera"] = sum(c["estado"] == "esperando" for c in sitio["camaras"])
        sitio["fuera_horario"] = sum(c["estado"] == "programada" for c in sitio["camaras"])
        if atrasadas:
            cantidad = "1 cámara" if len(atrasadas) == 1 else f"{len(atrasadas)} cámaras"
            detalle = f"{cantidad} con captura atrasada o reloj fuera de hora."
            registrar_incidencia(incidencias, "aviso", sitio["nombre"], detalle, f"/viveros/{sitio['id']}")
        por_nodo = {}
        for camara in sitio["camaras"]:
            por_nodo.setdefault(camara["nodo_id"], []).append(camara)
        faltan_datos = any(c["activa"] and not c["capturada_en"] for c in sitio["camaras"])
        nodos = []
        for nodo in sitio["nodos"]:
            camaras = por_nodo.get(nodo["id"], [])
            if nodo["enlazado"] and not nodo["ultimo_contacto"]:
                faltan_datos = True
            if not nodo["enlazado"] and not nodo["tiene_entrada"]:
                faltan_datos |= any(c["activa"] for c in camaras)
            nodos.append(diagnostico_nodo(nodo, camaras, sitio, ahora, minimo, incidencias))
        sitio["nodos"] = nodos
        nivel_inicial = "sin_datos" if faltan_datos or not sitio["activas"] else "ok"
        sitio["nivel"] = max((i["nivel"] for i in incidencias[primera_incidencia:]),
                             key=SEVERIDAD.get, default=nivel_inicial)

    incidencias.sort(key=lambda item: -SEVERIDAD[item["nivel"]])
    activas = sum(s["activas"] for s in sitios)
    if incidencias:
        nivel = incidencias[0]["nivel"]
        general = "Requiere intervención" if nivel == "critico" else "Requiere atención"
    elif not activas:
        nivel, general = "sin_datos", "Sin cámaras activas"
    elif any(s["activas"] and s["nivel"] == "sin_datos" for s in sitios) or any(
        m["nivel"] == "sin_datos" for m in servidor
    ):
        nivel, general = "sin_datos", "A la espera de datos"
    else:
        nivel, general = "ok", "Operación normal"

    return {
        "ahora": ahora, "general": general, "nivel": nivel, "servidor": servidor, "colas": colas,
        "sitios": sitios, "incidencias": incidencias, "criticas": sum(i["nivel"] == "critico" for i in incidencias),
        "avisos": sum(i["nivel"] == "aviso" for i in incidencias),
    }
