"""Reemplazo de los temporizadores de systemd (carpeta systemd/) dentro del contenedor «tareas».

Cada tarea corre en su propio hilo con la misma cadencia que su .timer: una espera inicial y luego
un intervalo contado desde que terminó la ejecución anterior (OnUnitInactiveSec), así una tarea
nunca se superpone consigo misma. El respaldo se programa a diario a las 02:20 hora local; si el
contenedor estaba detenido a esa hora se programa al arrancar (como Persistent=true).
"""

from datetime import datetime, timedelta
import signal
import subprocess
import sys
import threading

DETENER = threading.Event()

# accion, espera inicial (s), intervalo tras terminar (s), límite de ejecución (s), nice
PERIODICAS = (
    ("importar", 15, 60, 10 * 60, 0),
    ("analizar", 24, 15, 2 * 60, 8),
    ("media", 32, 20, 15 * 60, 12),
    ("alertas", 40, 60, 2 * 60, 4),
    ("mantenimiento", 15, 10, 8 * 3600, 15),
)
RESPALDO_HORA = (2, 20)


def registrar(mensaje):
    print(f"[tareas] {mensaje}", flush=True)


def ejecutar(accion, limite, nice):
    comando = ["nice", "-n", str(nice), sys.executable, "-m", "mgv_central", accion]
    try:
        resultado = subprocess.run(comando, cwd="/app", timeout=limite)
    except subprocess.TimeoutExpired:
        registrar(f"{accion}: superó {limite} s y se interrumpió.")
        return
    if resultado.returncode:
        registrar(f"{accion}: terminó con código {resultado.returncode}.")


def periodica(accion, inicial, intervalo, limite, nice):
    if DETENER.wait(inicial):
        return
    while not DETENER.is_set():
        ejecutar(accion, limite, nice)
        DETENER.wait(intervalo)


def respaldo_diario():
    ultimo = None
    while not DETENER.is_set():
        ahora = datetime.now()
        if ahora.time() >= ahora.replace(hour=RESPALDO_HORA[0], minute=RESPALDO_HORA[1]).time() \
                and ultimo != ahora.date():
            # respaldo-programado solo encola el respaldo del día (clave única), repetirlo no duplica.
            ejecutar("respaldo-programado", 8 * 3600, 15)
            ultimo = ahora.date()
        manana = (ahora + timedelta(days=1)).replace(hour=0, minute=0, second=5, microsecond=0)
        DETENER.wait(min(60, max(1, (manana - ahora).total_seconds())))


def main():
    signal.signal(signal.SIGTERM, lambda *_: DETENER.set())
    signal.signal(signal.SIGINT, lambda *_: DETENER.set())
    hilos = [threading.Thread(target=periodica, args=t, name=t[0], daemon=True) for t in PERIODICAS]
    hilos.append(threading.Thread(target=respaldo_diario, name="respaldo", daemon=True))
    for hilo in hilos:
        hilo.start()
    registrar("Temporizadores en marcha: " + ", ".join(h.name for h in hilos) + ".")
    DETENER.wait()
    registrar("Deteniendo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
