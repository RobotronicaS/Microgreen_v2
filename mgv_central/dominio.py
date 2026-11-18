from datetime import UTC, date, datetime, time, timedelta
from math import isfinite
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}")
INTERVALOS = (1, 2, 5, 10, 15, 30, 60, 120, 240)
PUESTOS = tuple((f"H{h}-C{p:02d}", str(h)) for h in range(1, 4) for p in range(1, 6))
SERIES = {"temp_c": "Temperatura °C", "humedad_pct": "Humedad %", "lux": "Iluminación lx", "co2_ppm": "CO₂ ppm"}


def identificador(valor):
    if not ID.fullmatch(valor):
        raise ValueError("Usá un identificador de hasta 32 letras, números, guiones o guiones bajos.")
    return valor


def texto(valor, limite, obligatorio=False):
    resultado = valor.strip()
    if len(resultado) > limite or (obligatorio and not resultado) or "\x00" in resultado:
        raise ValueError(f"Revisá el texto del formulario. El máximo es {limite} caracteres.")
    return resultado


def zona(nombre):
    try:
        return ZoneInfo(nombre)
    except (ValueError, ZoneInfoNotFoundError):
        raise ValueError("Ingresá una zona horaria válida, por ejemplo America/Montevideo.") from None


def en_horario(hora, desde, hasta):
    if desde == hasta:
        return True
    if desde < hasta:
        return desde <= hora < hasta
    return hora >= desde or hora < hasta


def turnos(ahora, sitio_zona, intervalo, desde, hasta, duracion=timedelta(days=1)):
    limite = ahora - duracion
    paso = timedelta(minutes=intervalo)
    ultimo = datetime.fromtimestamp(int(ahora.timestamp()) // (intervalo * 60) * (intervalo * 60), UTC)
    while ultimo > limite:
        if en_horario(ultimo.astimezone(sitio_zona).time(), desde, hasta):
            yield ultimo
        ultimo -= paso


def fecha_formulario(valor):
    try:
        return date.fromisoformat(valor)
    except ValueError:
        raise ValueError("Ingresá una fecha válida.") from None


def hora_formulario(valor):
    try:
        hora = time.fromisoformat(valor)
        if hora.tzinfo or hora.second or hora.microsecond:
            raise ValueError
        return hora
    except ValueError:
        raise ValueError("Ingresá una hora en formato HH:MM.") from None


def rango_local(desde, hasta, sitio_zona, max_dias=366):
    primero, ultimo = fecha_formulario(desde), fecha_formulario(hasta)
    if not 0 <= (ultimo - primero).days < max_dias or ultimo.year >= 9999:
        raise ValueError(f"Elegí un rango de hasta {max_dias} días.")
    return (
        datetime.combine(primero, time.min, sitio_zona),
        datetime.combine(ultimo + timedelta(days=1), time.min, sitio_zona),
    )


def region(formulario):
    try:
        x, y, ancho, alto = (float(formulario[nombre]) / 100 for nombre in ("x", "y", "ancho", "alto"))
        if not (0 <= x < 1 and 0 <= y < 1 and 0.01 <= ancho <= 1 and 0.01 <= alto <= 1
                and x + ancho <= 1.000001 and y + alto <= 1.000001):
            raise ValueError
        return [x, y, ancho, alto]
    except (ValueError, KeyError):
        raise ValueError("La región debe quedar dentro de la foto y medir al menos 1 % por lado.") from None


def escala(formulario):
    campos = ("escala_cm", "escala_px", "escala_ancho", "escala_alto")
    if not any(formulario.get(campo) for campo in campos):
        return None
    try:
        cm, px, ancho, alto = (float(formulario[campo]) for campo in campos)
        if not (0 < cm <= 10000 and 0 < px <= max(ancho, alto) and 1 <= ancho <= 30000 and 1 <= alto <= 30000):
            raise ValueError
        return [cm, px, ancho, alto]
    except (ValueError, KeyError):
        raise ValueError("Completá la longitud de referencia, sus píxeles y el tamaño original de la foto.") from None


def sensor(fila):
    try:
        fecha = datetime.fromisoformat(str(fila["fecha"]))
        valores = [float(fila[k]) if fila.get(k) not in (None, "") else None for k in SERIES]
        if fecha.tzinfo is None or all(valor is None for valor in valores):
            raise ValueError
        for valor, (minimo, maximo) in zip(valores, [(-100, 150), (0, 100), (0, 1e7), (0, 1e6)]):
            if valor is not None and (not isfinite(valor) or not minimo <= valor <= maximo):
                raise ValueError
        return (fecha, *valores)
    except (KeyError, TypeError, ValueError):
        raise ValueError("Cada lectura requiere fecha ISO con zona horaria y al menos un valor válido.") from None


def guardar_sensores(conexion, vivero, origen, filas):
    registros = [(vivero, origen, *sensor(fila)) for fila in filas]
    if not registros:
        raise ValueError("Ingresá al menos una lectura.")
    with conexion.transaction(), conexion.cursor() as cursor:
        cursor.executemany(
            """INSERT INTO sensores (vivero_id, origen, fecha, temp_c, humedad_pct, lux, co2_ppm)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (vivero_id, origen, fecha) DO UPDATE SET temp_c = EXCLUDED.temp_c,
              humedad_pct = EXCLUDED.humedad_pct, lux = EXCLUDED.lux, co2_ppm = EXCLUDED.co2_ppm""",
            registros,
        )
    return len(registros)

