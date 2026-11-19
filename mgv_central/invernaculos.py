"""Canteros configurables y baja de invernáculos conservando el historial."""
from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.datastructures import FormData

from .acceso import exigir
from .base import auditar, conectar
from .configuracion import cargar_configuracion
from .dominio import texto
from .web import datos_formulario, fila, pagina, sitio, volver

rutas = APIRouter()
LIMITE_DIMENSION = 20


def dimension(valor):
    if not isinstance(valor, str) or not 1 <= len(valor) <= 2 or not valor.isascii() or not valor.isdecimal():
        raise ValueError('Ingresá una cantidad entera de filas y columnas entre 1 y 20.')
    numero = int(valor)
    if not 1 <= numero <= LIMITE_DIMENSION:
        raise ValueError('Ingresá una cantidad entera de filas y columnas entre 1 y 20.')
    return numero


def distribuir_canteros(registro, guardados):
    filas, columnas = registro['canteros_filas'], registro['canteros_columnas']
    por_posicion = {(c['fila'], c['columna']): c for c in guardados}
    canteros = []
    for f in range(1, filas + 1):
        for c in range(1, columnas + 1):
            ficha = dict(fila=f, columna=c, nombre='', planta='', variedad='', descripcion='')
            ficha.update(por_posicion.get((f, c), {}))
            ficha['etiqueta'] = ficha['nombre'] or f'Cantero {f}.{c}'
            ficha['x'] = 110 + (c - 1) * (548 / columnas)
            ficha['y'] = 130 + (f - 1) * 105
            ficha['ancho'] = 548 / columnas - (8 if columnas > 1 else 0)
            canteros.append(ficha)
    registro['canteros'] = canteros
    registro['canteros_reservados'] = sum(c['fila'] > filas or c['columna'] > columnas for c in guardados)
    return registro


def cargar_canteros(conexion, registro):
    guardados = conexion.execute(
        'SELECT * FROM canteros WHERE vivero_id = %s ORDER BY fila, columna', (registro['id'],),
    ).fetchall()
    return distribuir_canteros(registro, guardados)


def bloquear_invernaculo(conexion, vivero):
    return fila(conexion, 'SELECT * FROM viveros WHERE id = %s AND eliminado_en IS NULL FOR UPDATE',
                (vivero,), 'Invernáculo desconocido.')


@rutas.get('/viveros/{vivero}/canteros')
def ver_canteros(peticion: Request, vivero: str, guardado: str = ''):
    with conectar(cargar_configuracion()) as conexion:
        usuario = exigir(conexion, peticion, vivero)
        registro = cargar_canteros(conexion, sitio(conexion, vivero))
    return pagina(peticion, 'canteros.html', usuario, sitio=registro, guardado=guardado,
                  titulo='Canteros de ' + registro['nombre'])


@rutas.post('/viveros/{vivero}/canteros/distribucion')
def guardar_distribucion(peticion: Request, vivero: str, datos: FormData = Depends(datos_formulario)):
    with conectar(cargar_configuracion()) as conexion:
        with conexion.transaction():
            usuario = exigir(conexion, peticion, vivero, editar=True, formulario=datos)
            bloquear_invernaculo(conexion, vivero)
            filas, columnas = dimension(datos.get('filas')), dimension(datos.get('columnas'))
            conexion.execute('UPDATE viveros SET canteros_filas = %s, canteros_columnas = %s WHERE id = %s',
                             (filas, columnas, vivero))
            auditar(conexion, usuario, vivero, 'distribuir canteros', f'{filas} filas, {columnas} columnas')
    return volver(f'/viveros/{vivero}/canteros?guardado=distribucion')


@rutas.post('/viveros/{vivero}/canteros/{numero_fila}/{columna}')
def guardar_cantero(peticion: Request, vivero: str, numero_fila: int, columna: int,
                    datos: FormData = Depends(datos_formulario)):
    with conectar(cargar_configuracion()) as conexion:
        with conexion.transaction():
            usuario = exigir(conexion, peticion, vivero, editar=True, formulario=datos)
            registro = bloquear_invernaculo(conexion, vivero)
            if not (1 <= numero_fila <= registro['canteros_filas'] and 1 <= columna <= registro['canteros_columnas']):
                raise HTTPException(409, 'La distribución cambió. Volvé a abrir los canteros.')
            valores = tuple(texto(datos.get(campo, ''), limite) for campo, limite in
                            (('nombre', 80), ('planta', 120), ('variedad', 120), ('descripcion', 1000)))
            conexion.execute(
                '''INSERT INTO canteros (vivero_id, fila, columna, nombre, planta, variedad, descripcion)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (vivero_id, fila, columna) DO UPDATE SET nombre = EXCLUDED.nombre,
                planta = EXCLUDED.planta, variedad = EXCLUDED.variedad,
                descripcion = EXCLUDED.descripcion, actualizada_en = now()''',
                (vivero, numero_fila, columna, *valores),
            )
            auditar(conexion, usuario, vivero, 'guardar cantero', f'{numero_fila}.{columna}')
    return volver(f'/viveros/{vivero}/canteros?guardado=cantero#cantero-{numero_fila}-{columna}')


@rutas.get('/viveros/{vivero}/eliminar')
def confirmar_eliminacion(peticion: Request, vivero: str):
    with conectar(cargar_configuracion()) as conexion:
        usuario = exigir(conexion, peticion, vivero, admin=True)
        registro = sitio(conexion, vivero)
        cantidades = conexion.execute(
            '''SELECT (SELECT count(*) FROM nodos WHERE vivero_id = %s) AS nodos,
            (SELECT count(*) FROM camaras WHERE vivero_id = %s) AS camaras,
            (SELECT count(*) FROM capturas WHERE vivero_id = %s) AS fotos''', (vivero, vivero, vivero),
        ).fetchone()
    return pagina(peticion, 'eliminar_invernaculo.html', usuario, sitio=registro,
                  cantidades=cantidades, titulo='Eliminar invernáculo')


@rutas.post('/viveros/{vivero}/eliminar')
def eliminar_invernaculo(peticion: Request, vivero: str, datos: FormData = Depends(datos_formulario)):
    with conectar(cargar_configuracion()) as conexion:
        with conexion.transaction():
            usuario = exigir(conexion, peticion, vivero, admin=True, formulario=datos)
            registro = bloquear_invernaculo(conexion, vivero)
            if datos.get('confirmacion', '').strip() != registro['nombre']:
                raise ValueError('Escribí el nombre completo del invernáculo para confirmar su eliminación.')
            conexion.execute('UPDATE viveros SET eliminado_en = now() WHERE id = %s', (vivero,))
            conexion.execute('UPDATE camaras SET activa = false, actualizada_en = now() WHERE vivero_id = %s', (vivero,))
            conexion.execute(
                "UPDATE altas_camara SET estado = 'error', terminada_en = now() WHERE vivero_id = %s AND estado IN ('pendiente', 'en_curso')",
                (vivero,),
            )
            conexion.execute(
                "UPDATE vinculaciones_zero SET estado = 'error', mensaje = 'Invernáculo eliminado.', token_sha = NULL, terminada_en = now(), actualizada_en = now() WHERE vivero_id = %s AND estado IN ('pendiente', 'en_curso')",
                (vivero,),
            )
            conexion.execute(
                "UPDATE trabajos SET estado = 'error', mensaje = 'Invernáculo eliminado.', fin_en = now() WHERE vivero_id = %s AND estado = 'pendiente'",
                (vivero,),
            )
            conexion.execute('UPDATE alertas SET cerrada_en = now() WHERE vivero_id = %s AND cerrada_en IS NULL', (vivero,))
            auditar(conexion, usuario, vivero, 'eliminar invernáculo', registro['nombre'])
    return volver('/?eliminado=si')
