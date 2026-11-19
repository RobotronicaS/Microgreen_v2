"""Edición de canteros, permisos y baja sin perder capturas existentes."""
from hashlib import sha256

import pytest
from starlette.requests import Request

from mgv_central.base import conectar, crear_esquema, registrar_catalogo
from mgv_central.invernaculos import dimension, distribuir_canteros
from mgv_central.web import pagina
from mgv_central.visual import preparar_plano
from pruebas.test_operacion import cliente, entorno, formulario, postgres, sistema


@pytest.mark.parametrize('valor', ['', '0', '-1', '21', '2.5', 'nan', '1e1', '１', None, '9' * 100])
def test_dimension_invalida(valor):
    with pytest.raises(ValueError):
        dimension(valor)


def test_cuadricula_conserva_posiciones_y_no_mutua_variedades():
    guardados = [dict(fila=2, columna=3, nombre='Fondo', planta='Lechuga', variedad='Crespa')]
    grande = distribuir_canteros(dict(canteros_filas=2, canteros_columnas=3), guardados)
    assert len(grande['canteros']) == 6 and grande['canteros'][-1]['variedad'] == 'Crespa'
    chico = distribuir_canteros(dict(canteros_filas=1, canteros_columnas=1), guardados)
    assert len(chico['canteros']) == 1 and chico['canteros_reservados'] == 1
    assert guardados[0]['variedad'] == 'Crespa'
    assert dimension('20') == 20 and dimension('1') == 1


@pytest.mark.parametrize('rol,administrador', [('tecnico', True), ('tecnico', False), ('consulta', False)])
def test_fichas_renderizadas_escapan_texto_y_respetan_el_rol(rol, administrador):
    registro = distribuir_canteros(dict(id='piloto', nombre='Piloto', canteros_filas=2, canteros_columnas=3),
                                  [dict(fila=2, columna=3, planta='Lechuga', variedad='Crespa', descripcion='<script>prueba</script>')])
    request = Request(dict(type='http', path='/viveros/piloto/canteros', headers=[], query_string=b''))
    usuario = dict(nombre='prueba', rol=rol, administrador=administrador, csrf='csrf-de-prueba')
    html = pagina(request, 'canteros.html', usuario, sitio=registro, guardado='').body.decode('utf-8')
    assert html.count('class="panel cantero-card"') == 6
    assert 'Crespa' in html and '&lt;script&gt;prueba&lt;/script&gt;' in html
    assert '<script>prueba</script>' not in html
    assert ('Guardar distribución' in html) == (rol == 'tecnico')
    assert ('name="descripcion"' in html) == (rol == 'tecnico')


def test_plano_renderiza_distribucion_y_variedades():
    registro = distribuir_canteros(dict(id='piloto', nombre='Piloto', ubicacion='', canteros_filas=2,
                                       canteros_columnas=3, nodos=[], camaras=[]),
                                  [dict(fila=1, columna=2, planta='Rábano', variedad='Sango')])
    request = Request(dict(type='http', path='/viveros/piloto', headers=[], query_string=b''))
    html = pagina(request, 'vivero.html', dict(nombre='admin', rol='tecnico', administrador=True, csrf='csrf-prueba'),
                  sitio=registro, plano=preparar_plano(registro), vista_camara='', vista_zero='').body.decode('utf-8')
    assert html.count('data-cantero=') == 6
    assert 'Rábano' in html and 'Sango' in html and 'Eliminar invernáculo' in html


def test_canteros_persisten_y_aparecen_en_el_plano(sistema):
    ruta = '/viveros/piloto/canteros'
    with cliente(sistema, 'tecnico') as tecnico:
        r = tecnico.post(ruta + '/distribucion', data=formulario(sistema, cuenta='tecnico', filas=2, columnas=3), follow_redirects=False)
        assert r.status_code == 303
        datos = dict(nombre='Cantero del fondo', planta='Lechuga', variedad='Crespa verde', descripcion='<script>alert(1)</script>\nVariedad de hoja tierna.')
        r = tecnico.post(ruta + '/2/3', data=formulario(sistema, cuenta='tecnico', **datos), follow_redirects=False)
        assert r.status_code == 303
        pagina = tecnico.get(ruta).text
        assert 'Información del cantero' not in pagina
        assert 'Crespa verde' in pagina and '&lt;script&gt;' in pagina
        assert '<script>alert(1)</script>' not in pagina
        plano = tecnico.get('/viveros/piloto').text
        assert plano.count('data-cantero=') == 6
        assert 'Cantero del fondo' in plano and 'Crespa verde' in plano
        assert tecnico.post(ruta + '/distribucion', data=formulario(sistema, cuenta='tecnico', filas=1, columnas=1)).status_code == 200
        assert 'fuera de la distribución actual' in tecnico.get(ruta).text
        assert tecnico.post(ruta + '/2/3', data=formulario(sistema, cuenta='tecnico', **datos)).status_code == 409
        tecnico.post(ruta + '/distribucion', data=formulario(sistema, cuenta='tecnico', filas=2, columnas=3))
        assert 'Crespa verde' in tecnico.get(ruta).text
    with conectar(sistema.config) as c:
        crear_esquema(c); crear_esquema(c)
        ficha = c.execute("SELECT nombre, planta, variedad, descripcion FROM canteros WHERE vivero_id='piloto' AND fila=2 AND columna=3").fetchone()
        assert ficha == datos


def test_permisos_y_validacion_de_canteros(sistema):
    ruta = '/viveros/piloto/canteros'
    with cliente(sistema, 'lector') as lector:
        assert lector.get(ruta).status_code == 200
        assert 'Guardar distribución' not in lector.get(ruta).text
        assert lector.post(ruta + '/distribucion', data=formulario(sistema, cuenta='lector', filas=2, columnas=3)).status_code == 403
        assert lector.post(ruta + '/1/1', data=formulario(sistema, cuenta='lector', variedad='Otra')).status_code == 403
    with cliente(sistema) as admin:
        assert admin.post(ruta + '/distribucion', data=dict(filas=2, columnas=3)).status_code == 403
        for valor in ('0', '-1', '21', '2.5', 'nan', ''):
            assert admin.post(ruta + '/distribucion', data=formulario(sistema, filas=valor, columnas=1)).status_code == 400
        assert admin.post(ruta + '/1/1', data=formulario(sistema, variedad='x' * 121)).status_code == 400
        assert admin.post('/viveros/inexistente/canteros/distribucion', data=formulario(sistema, filas=1, columnas=1)).status_code == 404
    with conectar(sistema.config) as c:
        assert c.execute("SELECT canteros_filas,canteros_columnas FROM viveros WHERE id='piloto'").fetchone() == dict(canteros_filas=3,canteros_columnas=1)
        assert c.execute('SELECT count(*) AS n FROM canteros').fetchone()['n'] == 0


def test_eliminar_exige_administrador_csrf_y_nombre(sistema):
    ruta = '/viveros/piloto/eliminar'
    for cuenta in ('tecnico', 'lector'):
        with cliente(sistema, cuenta) as usuario:
            assert usuario.get(ruta).status_code == 403
            assert usuario.post(ruta, data=formulario(sistema, cuenta=cuenta, confirmacion='Vivero piloto')).status_code == 403
    with cliente(sistema) as admin:
        assert admin.get(ruta).status_code == 200
        assert admin.post(ruta, data=dict(confirmacion='Vivero piloto')).status_code == 403
        assert admin.post(ruta, data=formulario(sistema, confirmacion='Otro')).status_code == 400
        assert admin.get('/viveros/piloto').status_code == 200
    with conectar(sistema.config) as c:
        assert c.execute("SELECT eliminado_en FROM viveros WHERE id='piloto'").fetchone()['eliminado_en'] is None


def test_baja_oculta_pausa_y_conserva_historial(sistema):
    foto = sistema.config.archivo / 'original.jpg'
    foto.write_bytes(b'imagen-existente-de-prueba')
    with conectar(sistema.config) as c:
        c.execute("INSERT INTO capturas(vivero_id,camara_id,capturada_en,clave_archivo,sha256,bytes,ancho,alto) VALUES('piloto','H1-C01',now(),'original.jpg',%s,%s,640,480)", (sha256(foto.read_bytes()).hexdigest(), foto.stat().st_size))
        c.execute("INSERT INTO viveros(id,nombre) VALUES('otro','Otro invernáculo')")
        c.execute("INSERT INTO canteros(vivero_id,fila,columna,variedad) VALUES('piloto',1,1,'Crespa')")
        c.execute("INSERT INTO trabajos(vivero_id,tipo,datos) VALUES('piloto','analisis','{}')")
        c.execute("INSERT INTO alertas(clave,vivero_id,nivel,detalle) VALUES('prueba','piloto','aviso','Prueba')")
    with cliente(sistema) as admin:
        assert 'Eliminar invernáculo' in admin.get('/viveros/piloto').text
        r = admin.post('/viveros/piloto/eliminar', data=formulario(sistema, confirmacion='Vivero piloto'), follow_redirects=False)
        assert r.status_code == 303 and r.headers['location'] == '/?eliminado=si'
        for ruta in ('/viveros/piloto', '/viveros/piloto/canteros', '/viveros/piloto/ajustes', '/viveros/piloto/camaras/H1-C01'):
            assert admin.get(ruta).status_code == 404
        assert 'Vivero piloto' not in admin.get('/').text
        assert 'Otro invernáculo' in admin.get('/').text
        assert admin.get('/viveros/otro').status_code == 200
        enc = {'Authorization': 'Bearer ' + sistema.token_nodo}
        politica = admin.get('/api/nodos/piloto/zero01/config', headers=enc)
        assert politica.status_code == 200 and all(not c['activa'] for c in politica.json()['camaras'])
        with conectar(sistema.config) as c:
            c.execute("UPDATE camaras SET activa=true WHERE vivero_id='piloto'")
        assert all(not c['activa'] for c in admin.get('/api/nodos/piloto/zero01/config', headers=enc).json()['camaras'])
        assert admin.post('/api/nodos/piloto/zero01/estado', headers=enc, json={}).status_code == 401
        assert admin.post('/viveros/piloto/canteros/1/1', data=formulario(sistema, variedad='Otra')).status_code == 404
    with cliente(sistema, 'lector') as lector:
        assert 'Vivero piloto' not in lector.get('/').text
        assert lector.get('/viveros/piloto').status_code == 404
    with conectar(sistema.config) as c:
        crear_esquema(c); registrar_catalogo(c, sistema.config)
        assert c.execute("SELECT eliminado_en FROM viveros WHERE id='piloto'").fetchone()['eliminado_en'] is not None
        assert c.execute('SELECT count(*) AS n FROM capturas').fetchone()['n'] == 1
        assert c.execute('SELECT variedad FROM canteros').fetchone()['variedad'] == 'Crespa'
        assert c.execute('SELECT count(*) AS n FROM usuarios').fetchone()['n'] == 3
        assert c.execute('SELECT estado FROM trabajos').fetchone()['estado'] == 'error'
        assert c.execute('SELECT cerrada_en FROM alertas').fetchone()['cerrada_en'] is not None
        assert c.execute("SELECT count(*) AS n FROM auditoria WHERE accion='eliminar invernáculo'").fetchone()['n'] == 1
    assert foto.read_bytes() == b'imagen-existente-de-prueba'
