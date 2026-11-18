"""Contrato real del plano, permisos, expansión y reconfiguración v1."""
from datetime import UTC, datetime
import importlib
import json
from pathlib import Path

from mgv_central.base import conectar, crear_esquema
from pruebas.test_operacion import cliente, entorno, formulario, postgres, sistema
from pruebas.carga_150 import ejemplo, nombre


def test_vistas_reales_y_permisos(sistema):
    with cliente(sistema) as admin:
        for ruta in ('/', '/viveros/piloto', '/configuracion', '/invernaculos/nuevo',
                     '/viveros/piloto/dispositivos/nuevo', '/viveros/piloto/camaras/H1-C01/configurar'):
            r = admin.get(ruta)
            assert r.status_code == 200, (ruta, r.text[:300])
            assert 'PROTOTIPO' not in r.text and 'config.json' not in r.text
        assert 'data-mapa' in admin.get('/viveros/piloto').text
        assert 'sin conexión con equipos reales' not in admin.get('/').text
    with cliente(sistema, 'lector') as lector:
        r = lector.get('/viveros/piloto')
        assert r.status_code == 200 and 'data-organizar' not in r.text
        assert 'Agregar dispositivo' not in r.text
        for ruta in ('/invernaculos/nuevo', '/viveros/piloto/dispositivos/nuevo', '/viveros/piloto/camaras/H1-C01/configurar'):
            assert lector.get(ruta).status_code == 403
        assert lector.post('/api/viveros/piloto/plano', data=formulario(sistema, cuenta='lector', tipo='camara', id='H1-C01', x=.5, y=.5)).status_code == 403


def test_plano_persistente_y_validacion(sistema):
    api = '/api/viveros/piloto/plano'
    with cliente(sistema) as admin:
        for tipo, codigo in [('camara', 'H1-C01'), ('zero', 'zero01')]:
            r = admin.post(api, data=formulario(sistema, tipo=tipo, id=codigo, x=.25, y=.65))
            assert r.status_code == 200 and r.json()['guardado']
        for x in ('nan', 'inf', '-1', '1.01', '0.01', 'no-numero'):
            assert admin.post(api, data=formulario(sistema, tipo='camara', id='H1-C01', x=x, y=.5)).status_code == 400
        assert admin.post(api, data=dict(tipo='camara', id='H1-C01', x=.5, y=.5)).status_code == 403
        assert admin.post(api, data=formulario(sistema, tipo='camara', id='inexistente', x=.5, y=.5)).status_code == 404
        assert 'translate(230.0 351.0)' in admin.get('/viveros/piloto').text
    with conectar(sistema.config) as c:
        assert c.execute("SELECT plano_x, plano_y FROM camaras WHERE vivero_id='piloto'").fetchone() == dict(plano_x=.25, plano_y=.65)
        crear_esquema(c)
        crear_esquema(c)


def test_expansion_invernaculos_y_zeros(sistema):
    with cliente(sistema) as admin:
        for i in range(12):
            r = admin.post('/gestion/viveros', data=formulario(sistema, nombre=f'Invernáculo {i}', zona='America/Montevideo', vista='plano'), follow_redirects=False)
            assert r.status_code == 303 and r.headers['location'].startswith('/viveros/')
        for i in range(3):
            r = admin.post('/viveros/piloto/equipos/nuevo', data=formulario(sistema, nombre=f'Zero adicional {i}'), follow_redirects=False)
            assert r.status_code == 303 and r.headers['location'].endswith('/conectar')
        assert admin.get('/viveros/piloto').status_code == 200
    with conectar(sistema.config) as c:
        assert c.execute('SELECT count(*) AS n FROM viveros').fetchone()['n'] == 13
        assert c.execute("SELECT count(*) AS n FROM nodos WHERE vivero_id='piloto'").fetchone()['n'] == 4


def test_alta_sin_limite_de_15_y_reconfiguracion_segura(sistema, monkeypatch):
    raiz = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(raiz/'zero'/'base'))
    monkeypatch.syspath_prepend(str(raiz/'zero'))
    remota = importlib.import_module('alta_remota')
    zero = sistema.raiz/'zero-v1'; zero.mkdir()
    anterior = dict(destino=dict(habilitado=False), camaras=[dict(id='H1-C01', nombre='Anterior', rtsp='rtsp://u:vieja@192.168.1.42/stream1')])
    (zero/'config.json').write_text(json.dumps(anterior))
    monkeypatch.setattr(remota, 'validar_red', lambda url: None)
    instante = datetime.now(UTC)
    def foto(camara, carpeta):
        ruta = carpeta/camara['id']/instante.date().isoformat()/nombre(instante)
        ruta.parent.mkdir(parents=True); ruta.write_bytes(ejemplo(22))
        return ruta, 'ok'
    enc = {'Authorization':'Bearer '+sistema.token_nodo, 'Content-Type':'application/json'}
    with conectar(sistema.config) as c:
        c.execute("UPDATE nodos SET alta_en=now(), reporte=%s::jsonb WHERE vivero_id='piloto'", (json.dumps({'capacidades':['capturar','reconfigurar','intervalos_v1']}),))
    with cliente(sistema) as admin:
        def llamada(ruta, contenido):
            r = admin.post('/api/nodos/piloto/zero01'+ruta, headers=enc, content=contenido)
            assert r.status_code == 200, r.text
            return r.json()
        reporte=admin.post('/api/nodos/piloto/zero01/estado',headers=enc,json={'libres_bytes':1024**3,'pendientes':0,'camaras':{},'capacidades':['capturar','reconfigurar','intervalos_v1'],'version':'1.0.0'})
        assert reporte.status_code == 200
        with conectar(sistema.config) as c:
            reportado=c.execute("SELECT reporte FROM nodos WHERE vivero_id='piloto' AND id='zero01'").fetchone()['reporte']
            assert reportado['version']=='1.0.0' and 'reconfigurar' in reportado['capacidades']
        datos = formulario(sistema, accion='reconfigurar', nodo='zero01', camara='H1-C01', tipo='rtsp', rtsp='rtsp://u:nueva@192.168.1.50/otro', rotacion='90', ancho='640')
        assert admin.get('/viveros/piloto/alta-camara?camara=H1-C01').status_code == 200
        r = admin.post('/api/viveros/piloto/altas', data=datos)
        assert r.status_code == 202, r.text
        monkeypatch.setattr(remota,'capturar',lambda c,p:(None,'Falló la foto de prueba.'))
        remota.atender_altas(zero,llamada)
        assert json.loads((zero/'config.json').read_text()) == anterior
        monkeypatch.setattr(remota,'capturar',foto)
        r = admin.post('/api/viveros/piloto/altas',data=datos)
        assert r.status_code == 202
        jpg = remota.atender_altas(zero,llamada)
        assert jpg.is_file()
        actual = json.loads((zero/'config.json').read_text())['camaras'][0]
        assert 'nueva@' in actual['rtsp'] and actual['rotacion']==90 and actual['ancho']==640
        assert 'nueva@' not in admin.get('/api/viveros/piloto/altas/'+r.json()['id']).text
        for i in range(16,20):
            d = formulario(sistema,accion='agregar',nodo='zero01',camara=f'CAM{i:03d}',hilera='Hilera libre',tipo='rtsp',rtsp=f'rtsp://u:p@192.168.1.{i}/s')
            r=admin.post('/api/viveros/piloto/altas',data=d)
            assert r.status_code==202,r.text
            jid=r.json()['id']
            resultado=admin.post(f'/api/nodos/piloto/zero01/altas/{jid}/resultado',headers=enc,json={'ok':True,'foto':nombre(instante)})
            assert resultado.json()['aceptada']
        assert 'CAM019' in admin.get('/viveros/piloto').text
    with conectar(sistema.config) as c:
        crear_esquema(c); crear_esquema(c)


def test_cambios_de_programacion_y_capacidades(sistema):
    ruta='/viveros/piloto/camaras'
    d=formulario(sistema,id='H1-C01',nodo='zero01',nombre='Renombrada',hilera='2',activa='si',intervalo='5',atraso='20',desde='08:00',hasta='20:00')
    with cliente(sistema) as admin:
        assert admin.post(ruta,data=d,follow_redirects=False).status_code==409
        with conectar(sistema.config) as c:
            c.execute("UPDATE nodos SET reporte = %s::jsonb WHERE vivero_id='piloto'", (json.dumps({'capacidades':['intervalos_v1']}),))
        assert admin.post(ruta,data=d,follow_redirects=False).status_code==303
        assert 'Renombrada' in admin.get('/viveros/piloto').text
        with conectar(sistema.config) as c:
            c.execute("INSERT INTO nodos(vivero_id,id,nombre) VALUES('piloto','zero02','Otra Zero')")
        assert admin.post(ruta,data={**d,'nodo':'zero02'},follow_redirects=False).status_code==409
