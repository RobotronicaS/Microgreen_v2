from pathlib import Path

import psycopg
from psycopg.rows import dict_row


def conectar(configuracion, nombre=None):
    db = configuracion.base_datos
    return psycopg.connect(
        dbname=nombre or db.nombre,
        user=db.usuario,
        password=db.clave.get_secret_value(),
        host=db.host,
        port=db.puerto,
        connect_timeout=5,
        autocommit=True,
        row_factory=dict_row,
    )


def crear_esquema(conexion):
    sql = Path(__file__).with_name("esquema.sql").read_text(encoding="utf-8")
    with conexion.transaction():
        conexion.execute(sql)


def registrar_catalogo(conexion, configuracion):
    with conexion.transaction():
        for vivero in configuracion.viveros:
            conexion.execute(
                """
                INSERT INTO viveros (id, nombre, zona) VALUES (%s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (vivero.id, vivero.nombre, configuracion.zona_horaria),
            )
            for nodo in vivero.nodos:
                conexion.execute(
                    """
                    INSERT INTO nodos (vivero_id, id, nombre, entrada_legacy)
                    VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING
                    """,
                    (vivero.id, nodo.id, nodo.id, str(nodo.entrada)),
                )
                for camara in nodo.camaras:
                    conexion.execute(
                        """
                        INSERT INTO camaras (vivero_id, id, nodo_id, nombre, hilera, atraso_minutos)
                        VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING
                        """,
                        (vivero.id, camara.id, nodo.id, camara.nombre, camara.hilera, camara.atraso_minutos),
                    )
        conexion.execute(
            """INSERT INTO camaras_origen (vivero_id, nodo_id, camara_id)
            SELECT c.vivero_id, c.nodo_id, c.id FROM camaras c
            JOIN nodos n ON n.vivero_id = c.vivero_id AND n.id = c.nodo_id ON CONFLICT DO NOTHING""",
        )


def auditar(conexion, usuario, vivero, accion, detalle=""):
    conexion.execute(
        "INSERT INTO auditoria (usuario_id, vivero_id, accion, detalle) VALUES (%s, %s, %s, %s)",
        (usuario["id"], vivero, accion, detalle),
    )
