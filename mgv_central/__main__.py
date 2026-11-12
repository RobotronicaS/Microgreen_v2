import argparse
import os
from pathlib import Path
import subprocess
import sys

import psycopg

from .acceso import preparar_admin
from .avisos import supervisar
from .base import conectar
from .configuracion import cargar_configuracion
from .ingesta import importar
from .preparar import preparar
from .procesos import procesar
from .respaldo import respaldar, dump_base, verificar


def main():
    parser = argparse.ArgumentParser(description="MicroGreenVision Central")
    parser.add_argument("accion", choices=(
        "preparar", "importar", "analizar", "media", "alertas", "clave-admin",
        "respaldar", "volcar-base", "verificar",
    ))
    parser.add_argument("--destino")
    parser.add_argument("--archivo")
    parser.add_argument("--base")
    opciones = parser.parse_args()
    os.umask(0o077)
    try:
        if opciones.accion == "preparar":
            preparar()
            return 0
        config = cargar_configuracion()
        if opciones.accion == "importar":
            resultado = importar(config)
            if resultado:
                print(f"{resultado['nuevas']} JPG incorporados · {resultado['rechazadas']} rechazados · "
                      f"{resultado['errores']} errores de lectura o archivo.", flush=True)
                if resultado["detalle"]:
                    print(resultado["detalle"], flush=True)
        elif opciones.accion in ("analizar", "media"):
            cantidad = procesar(config, media=opciones.accion == "media")
            if cantidad:
                print(f"{cantidad} trabajos procesados.", flush=True)
        elif opciones.accion == "alertas":
            supervisar(config)
        elif opciones.accion == "clave-admin":
            with conectar(config) as conexion:
                clave = preparar_admin(conexion, renovar=True)
            print(f"Usuario admin · nueva contraseña {clave}")
        elif opciones.accion == "respaldar":
            respaldar(config, opciones.destino)
        elif opciones.accion == "volcar-base":
            if not opciones.destino:
                raise ValueError("Indicá el archivo de destino con --destino.")
            dump_base(config, Path(opciones.destino))
        elif opciones.accion == "verificar":
            return 0 if verificar(config, opciones.archivo, opciones.base) else 1
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except psycopg.Error:
        print("PostgreSQL no pudo completar la operación. Revisá el servicio y config.json.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
