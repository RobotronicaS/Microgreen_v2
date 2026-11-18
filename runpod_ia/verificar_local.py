"""Prueba de API en memoria, con SAM2 real y sin publicar puertos.

La identidad de prueba se crea en una carpeta temporal y se elimina al salir.
No modifica ni genera el acceso de producción.
"""
import base64
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import secrets
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient
from PIL import Image

from .api import app


def main():
    original=Path('/workspace/planta_original.jpeg').read_bytes()
    with Image.open(BytesIO(original)) as im:
        im=im.convert('RGB'); im.thumbnail((1280,1280))
        b=BytesIO(); im.save(b,'JPEG',quality=85); foto=b.getvalue()
    with TemporaryDirectory(prefix='mgv-api-prueba-') as d:
        raiz=Path(d)
        token=secrets.token_urlsafe(32)
        (raiz/'token').write_text(token); (raiz/'token').chmod(0o600)
        os.environ['MGV_IA_TOKEN_FILE']=str(raiz/'token')
        os.environ['MGV_IA_CACHE']=str(raiz/'cache')
        with TestClient(app) as c:
            assert c.get('/salud').status_code==401
            h={'Authorization':'Bearer '+token}
            assert c.get('/salud',headers=h).json()['estado']=='ok'
            pedido=dict(idempotencia=hashlib.sha256(foto).hexdigest(),roi=[0,0,1,1],imagen=base64.b64encode(foto).decode())
            r=c.post('/v1/analizar',headers=h,json=pedido)
            assert r.status_code==200,r.text
            datos=r.json()
            assert datos['imagen_sha256']==hashlib.sha256(foto).hexdigest()
            assert c.post('/v1/analizar',headers=h,json=pedido).json()==datos
            salida=Path('/workspace/microgreen/resultados/api_sam2_verificada')
            salida.with_suffix('.png').write_bytes(base64.b64decode(datos.pop('vista_png')))
            datos.update(api_verificada=True, acceso_sin_clave_rechazado=True, reintento_identico=True)
            salida.with_suffix('.json').write_text(json.dumps(datos,ensure_ascii=False,indent=2))
            print(json.dumps(datos,ensure_ascii=False))

if __name__=='__main__':
    main()
