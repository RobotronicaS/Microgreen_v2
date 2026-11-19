"""Navegación ilustrada y distribución persistente de dispositivos."""
from datetime import UTC, datetime
from math import isfinite

from fastapi import APIRouter, Depends, Request
from starlette.datastructures import FormData

from .acceso import exigir
from .base import auditar, conectar
from .configuracion import cargar_configuracion
from .dominio import identificador, texto
from .gestion import preparar_equipo, registrar_nodo
from .resumen import captura_disponible
from .web import datos_formulario, fila, pagina, sitio, volver

rutas = APIRouter()


def preparar_plano(registro):
    """Coordenadas lógicas; la representación no es una medición física."""
    camaras, nodos = registro['camaras'], registro['nodos']
    filas = max(registro.get('canteros_filas', 3), (len(camaras) + 1) // 2)
    alto = max(540, 150 + filas * 105, 220 + len(nodos) * 115)
    ahora = datetime.now(UTC)
    for i, nodo in enumerate(nodos):
        preparar_equipo(nodo, [c for c in camaras if c['nodo_id'] == nodo['id']], ahora)
        nodo['puede_capturar'] = captura_disponible(nodo, ahora)
        nodo['px'] = (nodo.get('plano_x') or .87) * 920
        nodo['py'] = nodo.get('plano_y') * alto if nodo.get('plano_y') is not None else 150 + i * 115
    por_id = {n['id']: n for n in nodos}
    for i, camara in enumerate(camaras):
        camara['px'] = camara.get('plano_x') * 920 if camara.get('plano_x') is not None else 240 + (i % 2) * 285
        camara['py'] = camara.get('plano_y') * alto if camara.get('plano_y') is not None else 185 + (i // 2) * 105
        camara['equipo'] = por_id.get(camara['nodo_id'])
    return dict(alto=alto, filas=registro.get('canteros_filas', 3))


@rutas.get('/configuracion')
def configuracion(peticion: Request):
    with conectar(cargar_configuracion()) as conexion:
        usuario = exigir(conexion, peticion)
    return pagina(peticion, 'configuracion.html', usuario, titulo='Configuración')


@rutas.get('/invernaculos/nuevo')
def nuevo_invernaculo(peticion: Request):
    with conectar(cargar_configuracion()) as conexion:
        usuario = exigir(conexion, peticion, admin=True)
    return pagina(peticion, 'nuevo_invernaculo.html', usuario, titulo='Agregar invernáculo')


@rutas.get('/viveros/{vivero}/dispositivos/nuevo')
def nuevo_dispositivo(peticion: Request, vivero: str):
    with conectar(cargar_configuracion()) as conexion:
        usuario = exigir(conexion, peticion, vivero, editar=True)
        registro = sitio(conexion, vivero)
        nodos = conexion.execute('SELECT id, nombre FROM nodos WHERE vivero_id = %s ORDER BY nombre', (vivero,)).fetchall()
    return pagina(peticion, 'nuevo_dispositivo.html', usuario, sitio=registro, nodos=nodos, titulo='Agregar dispositivo')


@rutas.post('/viveros/{vivero}/equipos/nuevo')
def nuevo_equipo(peticion: Request, vivero: str, datos: FormData = Depends(datos_formulario)):
    with conectar(cargar_configuracion()) as conexion:
        usuario = exigir(conexion, peticion, vivero, admin=True, formulario=datos)
        nodo = registrar_nodo(conexion, usuario, vivero, texto(datos.get('nombre', ''), 80, True))
    return volver(f'/viveros/{vivero}/nodos/{nodo}/conectar')


@rutas.post('/api/viveros/{vivero}/plano')
def guardar_posicion(peticion: Request, vivero: str, datos: FormData = Depends(datos_formulario)):
    tipo = datos.get('tipo')
    if tipo not in ('camara', 'zero'):
        raise ValueError('Seleccioná una cámara o un equipo Zero.')
    codigo = identificador(datos.get('id', ''))
    try:
        x, y = float(datos.get('x', '')), float(datos.get('y', ''))
    except ValueError:
        raise ValueError('Posición inválida.') from None
    if not all(isfinite(v) and .07 <= v <= .93 for v in (x, y)):
        raise ValueError('Ubicá el dispositivo dentro del plano.')
    tabla = 'camaras' if tipo == 'camara' else 'nodos'
    with conectar(cargar_configuracion()) as conexion:
        usuario = exigir(conexion, peticion, vivero, editar=True, formulario=datos)
        with conexion.transaction():
            fila(conexion, f'UPDATE {tabla} SET plano_x = %s, plano_y = %s WHERE vivero_id = %s AND id = %s RETURNING id',
                 (x, y, vivero, codigo), 'Dispositivo desconocido.')
            auditar(conexion, usuario, vivero, 'mover dispositivo en plano', codigo)
    return {'guardado': True}


@rutas.post('/viveros/{vivero}/nodos/{nodo}/nombre')
def renombrar_equipo(peticion: Request, vivero: str, nodo: str, datos: FormData = Depends(datos_formulario)):
    with conectar(cargar_configuracion()) as conexion:
        usuario = exigir(conexion, peticion, vivero, admin=True, formulario=datos)
        nombre = texto(datos.get('nombre', ''), 80, True)
        with conexion.transaction():
            fila(conexion, 'UPDATE nodos SET nombre = %s WHERE vivero_id = %s AND id = %s RETURNING id',
                 (nombre, vivero, nodo), 'Equipo desconocido.')
            auditar(conexion, usuario, vivero, 'renombrar equipo', nodo)
    return volver(f'/viveros/{vivero}?zero={nodo}')


@rutas.get('/viveros/{vivero}/camaras/{camara}/configurar')
def configurar_camara(peticion: Request, vivero: str, camara: str):
    from .dominio import INTERVALOS
    from .web import puesto
    with conectar(cargar_configuracion()) as conexion:
        usuario = exigir(conexion, peticion, vivero, editar=True)
        registro = sitio(conexion, vivero)
        ficha = puesto(conexion, vivero, camara)
        nodos = conexion.execute('SELECT id, nombre FROM nodos WHERE vivero_id = %s ORDER BY nombre', (vivero,)).fetchall()
    return pagina(peticion, 'camara_configurar.html', usuario, sitio=registro, camara=ficha,
                  nodos=nodos, intervalos=INTERVALOS, titulo='Configurar cámara')
