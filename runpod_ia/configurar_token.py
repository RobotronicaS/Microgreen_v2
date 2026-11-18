"""Configuración interactiva: la clave la ingresa el operador, nunca el chat."""
from getpass import getpass
import os
from pathlib import Path

def main():
    ruta = Path(os.getenv("MGV_IA_TOKEN_FILE", "/workspace/microgreen/servicio_ia/token.txt"))
    clave = getpass("Clave de análisis (32 a 256 caracteres): ")
    if not 32 <= len(clave) <= 256 or not clave.isascii() or any(c.isspace() for c in clave):
        raise SystemExit("Usá de 32 a 256 caracteres, sin espacios.")
    if clave != getpass("Repetí la clave: "):
        raise SystemExit("Las claves no coinciden.")
    ruta.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(ruta, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(clave)
    os.chmod(ruta, 0o600)
    print("Acceso configurado. Ingresá la misma clave en Central; no la pegues en el chat.")

if __name__ == "__main__":
    main()
