"""Contrato HTTP del analizador y flujo Central → servicio → resultado persistente."""
import base64
import hashlib
from io import BytesIO
from types import SimpleNamespace

from fastapi.testclient import TestClient
import numpy as np
from PIL import Image
from psycopg.types.json import Jsonb
import pytest

from mgv_central import runpod
from mgv_central.base import conectar, crear_esquema
from mgv_central.procesos import procesar
from pruebas.test_operacion import cliente, entorno, formulario, postgres, sistema
from pruebas.carga_150 import ejemplo
from runpod_ia import api, motor


def png():
    b = BytesIO()
    Image.new('RGB',(30,20),(20,180,40)).save(b,'PNG')
    return b.getvalue()


@pytest.fixture
def servicio(tmp_path,monkeypatch):
    clave = 'clave-local-de-prueba-'+'x'*32
    token = tmp_path/'token'; token.write_text(clave)
    monkeypatch.setenv('MGV_IA_TOKEN_FILE',str(token))
    monkeypatch.setenv('MGV_IA_CACHE',str(tmp_path/'cache'))
    llamadas = []
    def modelo(imagen,roi):
        llamadas.append(roi)
        return dict(algoritmo=motor.ALGORITMO,cobertura=42.5,roi=list(roi),validacion='estimacion'),png()
    monkeypatch.setattr(api,'segmentar',modelo)
    with TestClient(api.app) as c:
        yield c,clave,llamadas


def test_api_autenticacion_y_deduplicacion(servicio):
    c,clave,llamadas = servicio
    assert c.get('/salud').status_code == 401
    h={'Authorization':'Bearer '+clave}
    assert c.get('/salud',headers=h).json()['algoritmo'] == motor.ALGORITMO
    d=dict(idempotencia='a'*64,imagen=base64.b64encode(ejemplo(1)).decode(),roi=[0,0,1,1])
    primero=c.post('/v1/analizar',headers=h,json=d)
    assert primero.status_code==200,primero.text
    assert c.post('/v1/analizar',headers=h,json=d).json()==primero.json()
    assert len(llamadas)==1
    assert c.post('/v1/analizar',headers=h,json={**d,'imagen':base64.b64encode(ejemplo(2)).decode()}).status_code==409
    for cambio in ({'imagen':'no-es-base64'}, {'idempotencia':'../otro'}, {'roi':[0,0,2,1]}):
        assert c.post('/v1/analizar',headers=h,json={**d,**cambio}).status_code==422


def test_sam2_union_sin_sumar_mascaras_superpuestas():
    imagen=Image.new('RGB',(40,20),(120,90,70))
    imagen.paste((20,180,40),(0,0,20,20))
    mascara=np.zeros((20,40),bool); mascara[:,:20]=True
    modelo=SimpleNamespace(generate=lambda rgb:[{'segmentation':mascara},{'segmentation':mascara.copy()}])
    resultado,vista=motor.segmentar(imagen,[0,0,1,1],generador=modelo)
    assert resultado['cobertura']==50 and resultado['regiones']==2
    assert vista.startswith(b'\x89PNG')


def test_sam2_no_interpreta_una_segmentacion_vacia_como_ausencia_de_verde():
    imagen=Image.new('RGB',(40,20),(20,180,40))
    vacio=SimpleNamespace(generate=lambda rgb:[])
    resultado,_=motor.segmentar(imagen,[0,0,1,1],generador=vacio)
    assert resultado['validacion']=='insuficiente'
    assert 'no uses este porcentaje' in resultado['advertencia']


@pytest.mark.parametrize('roi', [[0,0,float('nan'),1],[0,0,float('inf'),1],[-.1,0,1,1],[0,0,0,1],[0,0,2,1]])
def test_roi_invalida(roi):
    with pytest.raises(ValueError): motor.region_valida(roi)


@pytest.mark.parametrize('color', [(0,0,0),(150,150,150)])
def test_rechazo_fotos_oscuras_o_ir_sin_ejecutar_modelo(color):
    with pytest.raises(ValueError): motor.segmentar(Image.new('RGB',(20,20),color),[0,0,1,1])


@pytest.mark.parametrize('url', ['http://abc-8767.proxy.runpod.net','https://ejemplo.com','https://abc-8767.proxy.runpod.net/otra','https://u:p@abc-8767.proxy.runpod.net','https://127.0.0.1'])
def test_url_no_admitida(url):
    with pytest.raises(ValueError): runpod.AjustesIA(url=url)


def test_clave_no_se_muestra_y_configuracion_restringida(sistema):
    clave='secreto-de-prueba-'+'x'*32
    with cliente(sistema) as admin:
        d=formulario(sistema,url='https://abc-8767.proxy.runpod.net',token=clave,activo='si',espera='45')
        assert admin.post('/runpod',data=d,follow_redirects=False).status_code==303
        assert clave not in admin.get('/runpod').text
        assert runpod.archivo_ajustes().stat().st_mode & 0o777 == 0o600
        assert admin.post('/runpod',data={**d,'csrf':'incorrecto'}).status_code==403
        assert admin.post('/runpod',data={**d,'token':''},follow_redirects=False).status_code==303
        assert runpod.cargar_ajustes().token.get_secret_value()==clave
    with cliente(sistema,'tecnico') as tecnico:
        assert tecnico.get('/runpod').status_code==403
    with conectar(sistema.config) as c:
        assert clave not in str(c.execute('SELECT * FROM auditoria').fetchall())


@pytest.fixture
def foto_lote(sistema):
    contenido=ejemplo(1)
    clave='piloto/H1-C01/ensayo.jpg'
    ruta=sistema.config.archivo/'originales'/clave
    ruta.parent.mkdir(parents=True); ruta.write_bytes(contenido)
    with conectar(sistema.config) as c:
        foto=c.execute("""INSERT INTO capturas(vivero_id,camara_id,capturada_en,clave_archivo,sha256,bytes,ancho,alto)
          VALUES('piloto','H1-C01',now(),%s,%s,%s,320,240) RETURNING id""",(clave,hashlib.sha256(contenido).hexdigest(),len(contenido))).fetchone()['id']
        lote=c.execute("""INSERT INTO lotes(vivero_id,camara_id,nombre,especie,siembra,roi)
          VALUES('piloto','H1-C01','Ensayo IA','Rábano',current_date-1,%s) RETURNING id""",(Jsonb([0,0,1,1]),)).fetchone()['id']
    return foto,lote


def test_flujo_central_api_resultado_y_permisos(sistema,foto_lote,servicio,monkeypatch):
    foto,lote=foto_lote
    servicio_http,clave,llamadas=servicio
    runpod.guardar_ajustes(runpod.AjustesIA(activo=True,url='https://abc-8767.proxy.runpod.net',token=clave))
    def transporte(ajustes,ruta,datos=None):
        h={'Authorization':'Bearer '+ajustes.token.get_secret_value()}
        r=servicio_http.get(ruta,headers=h) if datos is None else servicio_http.post(ruta,headers=h,json=datos)
        assert r.status_code==200,r.text
        return r.json()
    monkeypatch.setattr(runpod,'llamada',transporte)
    with cliente(sistema,'lector') as lector:
        assert lector.post(f'/lotes/{lote}/runpod',data=formulario(sistema,'lector')).status_code==403
    with cliente(sistema) as admin:
        assert admin.post('/runpod/probar',data=formulario(sistema),follow_redirects=False).status_code==303
        assert admin.post(f'/lotes/{lote}/runpod',data=formulario(sistema),follow_redirects=False).status_code==303
        assert procesar(sistema.config)==1
        assert len(llamadas)==1
        assert '42.5' in admin.get(f'/lotes/{lote}/runpod').text
        assert admin.get(f'/lotes/{lote}/runpod/{foto}.png').status_code==200
        assert 'sam2.1-tiny-verde-v1' in admin.get(f'/lotes/{lote}/runpod.csv').text
        assert clave not in admin.get('/trabajos').text
        admin.post(f'/lotes/{lote}/runpod',data=formulario(sistema))
        assert procesar(sistema.config)==1 and len(llamadas)==1
    with conectar(sistema.config) as c:
        assert c.execute('SELECT count(*) n FROM resultados_ia').fetchone()['n']==1
        assert c.execute('SELECT count(*) n FROM mediciones').fetchone()['n']==0
        crear_esquema(c);crear_esquema(c)
        c.execute('UPDATE lotes SET revision=revision+1 WHERE id=%s',(lote,))
    with cliente(sistema) as admin:
        assert '42.5' not in admin.get(f'/lotes/{lote}/runpod').text
        assert admin.get(f'/lotes/{lote}/runpod/{foto}.png').status_code==404


def test_fallo_remoto_reintenta_sin_inventar_resultados(sistema,foto_lote,monkeypatch):
    foto,lote=foto_lote
    runpod.guardar_ajustes(runpod.AjustesIA(activo=True,url='https://abc-8767.proxy.runpod.net',token='x'*40))
    with conectar(sistema.config) as c: runpod.programar(c,None,'piloto',foto,lote,1)
    def fallar(*a,**kw): raise runpod.ErrorIA('El Pod no está disponible.')
    monkeypatch.setattr(runpod,'llamada',fallar)
    assert procesar(sistema.config)==1
    with conectar(sistema.config) as c:
        assert c.execute("SELECT estado FROM trabajos WHERE tipo='ia'").fetchone()['estado']=='pendiente'
        assert c.execute('SELECT count(*) n FROM resultados_ia').fetchone()['n']==0


def test_respuesta_de_otra_foto_o_mascara_invalida_rechazada():
    contenido=ejemplo(1); solicitud='a'*64
    r=dict(protocolo=1,algoritmo=motor.ALGORITMO,idempotencia=solicitud,validacion='estimacion',
           imagen_sha256=hashlib.sha256(contenido).hexdigest(),roi=[0,0,1,1],cobertura=50,vista_png=base64.b64encode(png()).decode())
    assert runpod.validar_resultado(r,solicitud,contenido,[0,0,1,1])[0]==50
    for cambio in ({'cobertura':float('nan')},{'imagen_sha256':'otra'},{'vista_png':'bm8='},{'roi':[0,0,float('nan'),1]},{'algoritmo':'modelo-desconocido'}):
        with pytest.raises(runpod.ErrorIA): runpod.validar_resultado({**r,**cambio},solicitud,contenido,[0,0,1,1])


def test_cliente_no_reenvia_credencial_tras_redireccion():
    assert runpod.SinRedireccion().redirect_request(None,None,302,'',{},'https://otro.example') is None


def test_automatico_requiere_activacion_y_no_duplica(sistema,foto_lote):
    foto,lote=foto_lote
    ajustes=runpod.AjustesIA(activo=True,url='https://abc-8767.proxy.runpod.net',token='x'*40)
    runpod.guardar_ajustes(ajustes)
    with conectar(sistema.config) as c:
        f=dict(id=foto,vivero_id='piloto'); l=[dict(id=lote,revision=1)]
        runpod.automatico(c,f,l)
        assert c.execute("SELECT count(*) n FROM trabajos WHERE tipo='ia'").fetchone()['n']==0
        ajustes.automatico=True;runpod.guardar_ajustes(ajustes)
        runpod.automatico(c,f,l);runpod.automatico(c,f,l)
        assert c.execute("SELECT count(*) n FROM trabajos WHERE tipo='ia'").fetchone()['n']==1

