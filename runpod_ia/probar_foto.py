"""Prueba local del modelo, sin publicar puertos ni crear credenciales."""
import argparse
import json
from pathlib import Path

from PIL import Image
from .motor import segmentar

def main():
    p = argparse.ArgumentParser()
    p.add_argument("imagen", type=Path)
    p.add_argument("--salida", type=Path, required=True)
    a = p.parse_args()
    with Image.open(a.imagen) as imagen:
        resultado, png = segmentar(imagen, [0,0,1,1])
    a.salida.parent.mkdir(parents=True, exist_ok=True)
    a.salida.with_suffix(".png").write_bytes(png)
    a.salida.with_suffix(".json").write_text(json.dumps(resultado, ensure_ascii=False, indent=2))
    print(json.dumps(resultado, ensure_ascii=False))

if __name__ == "__main__":
    main()
