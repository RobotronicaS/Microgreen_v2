CREATE TABLE IF NOT EXISTS viveros (
    id text PRIMARY KEY,
    nombre text NOT NULL
);

CREATE TABLE IF NOT EXISTS camaras (
    vivero_id text NOT NULL REFERENCES viveros(id),
    id text NOT NULL,
    nodo_id text NOT NULL,
    nombre text NOT NULL,
    hilera text NOT NULL,
    PRIMARY KEY (vivero_id, id)
);

CREATE TABLE IF NOT EXISTS capturas (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    vivero_id text NOT NULL,
    camara_id text NOT NULL,
    capturada_en timestamptz NOT NULL,
    incorporada_en timestamptz NOT NULL DEFAULT now(),
    clave_archivo text NOT NULL UNIQUE,
    sha256 char(64) NOT NULL,
    bytes bigint NOT NULL CHECK (bytes > 0),
    ancho integer NOT NULL CHECK (ancho > 0),
    alto integer NOT NULL CHECK (alto > 0),
    FOREIGN KEY (vivero_id, camara_id) REFERENCES camaras(vivero_id, id),
    UNIQUE (vivero_id, camara_id, capturada_en)
);

CREATE TABLE IF NOT EXISTS origenes (
    vivero_id text NOT NULL,
    nodo_id text NOT NULL,
    camara_id text NOT NULL,
    ruta text NOT NULL,
    mtime_ns bigint NOT NULL,
    bytes bigint NOT NULL,
    captura_id bigint REFERENCES capturas(id),
    problema text,
    PRIMARY KEY (vivero_id, nodo_id, camara_id, ruta),
    CHECK ((captura_id IS NULL) <> (problema IS NULL))
);

CREATE TABLE IF NOT EXISTS estado_importador (
    id boolean PRIMARY KEY DEFAULT true CHECK (id),
    terminado_en timestamptz NOT NULL,
    nuevas integer NOT NULL,
    rechazadas integer NOT NULL,
    errores integer NOT NULL,
    detalle text NOT NULL
);

ALTER TABLE viveros ADD COLUMN IF NOT EXISTS zona text NOT NULL DEFAULT 'America/Montevideo';
ALTER TABLE viveros ADD COLUMN IF NOT EXISTS ubicacion text NOT NULL DEFAULT '';
ALTER TABLE viveros ADD COLUMN IF NOT EXISTS creado_en timestamptz NOT NULL DEFAULT now();

CREATE TABLE IF NOT EXISTS nodos (
    vivero_id text NOT NULL REFERENCES viveros(id),
    id text NOT NULL,
    nombre text NOT NULL,
    entrada_legacy text,
    token_sha char(64),
    autorizado_en timestamptz,
    ultimo_contacto timestamptz,
    reporte jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY (vivero_id, id)
);

ALTER TABLE camaras ADD COLUMN IF NOT EXISTS activa boolean NOT NULL DEFAULT true;
ALTER TABLE camaras ADD COLUMN IF NOT EXISTS intervalo integer NOT NULL DEFAULT 30;
ALTER TABLE camaras ADD COLUMN IF NOT EXISTS atraso_minutos integer NOT NULL DEFAULT 65;
ALTER TABLE camaras ADD COLUMN IF NOT EXISTS desde time NOT NULL DEFAULT '00:00';
ALTER TABLE camaras ADD COLUMN IF NOT EXISTS hasta time NOT NULL DEFAULT '00:00';
ALTER TABLE camaras ADD COLUMN IF NOT EXISTS actualizada_en timestamptz NOT NULL DEFAULT now();
ALTER TABLE capturas ADD COLUMN IF NOT EXISTS recibida_en timestamptz;
ALTER TABLE origenes ADD COLUMN IF NOT EXISTS copia_rechazada text;

CREATE TABLE IF NOT EXISTS camaras_origen (
    vivero_id text NOT NULL,
    nodo_id text NOT NULL,
    camara_id text NOT NULL,
    PRIMARY KEY (vivero_id, nodo_id, camara_id),
    FOREIGN KEY (vivero_id, nodo_id) REFERENCES nodos(vivero_id, id),
    FOREIGN KEY (vivero_id, camara_id) REFERENCES camaras(vivero_id, id)
);

CREATE INDEX IF NOT EXISTS capturas_vivero_hora ON capturas (vivero_id, capturada_en DESC);
CREATE INDEX IF NOT EXISTS capturas_buscar_huella ON capturas (vivero_id, camara_id, sha256);
CREATE INDEX IF NOT EXISTS origenes_rechazados ON origenes (vivero_id, camara_id) WHERE problema IS NOT NULL;

CREATE TABLE IF NOT EXISTS usuarios (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nombre text NOT NULL UNIQUE,
    clave text NOT NULL,
    administrador boolean NOT NULL DEFAULT false,
    activo boolean NOT NULL DEFAULT true,
    creado_en timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS permisos (
    usuario_id bigint NOT NULL REFERENCES usuarios(id),
    vivero_id text NOT NULL REFERENCES viveros(id),
    rol text NOT NULL CHECK (rol IN ('tecnico', 'consulta')),
    PRIMARY KEY (usuario_id, vivero_id)
);
CREATE TABLE IF NOT EXISTS sesiones (
    token_sha char(64) PRIMARY KEY,
    usuario_id bigint NOT NULL REFERENCES usuarios(id),
    csrf text NOT NULL,
    vence_en timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS sesiones_vencimiento ON sesiones (vence_en);
CREATE TABLE IF NOT EXISTS intentos_login (
    clave char(64) PRIMARY KEY,
    intentos integer NOT NULL DEFAULT 0,
    desde timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS auditoria (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    usuario_id bigint REFERENCES usuarios(id),
    vivero_id text REFERENCES viveros(id),
    accion text NOT NULL,
    detalle text NOT NULL DEFAULT '',
    fecha timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS lotes (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    vivero_id text NOT NULL,
    camara_id text NOT NULL,
    nombre text NOT NULL,
    especie text NOT NULL,
    variedad text NOT NULL DEFAULT '',
    bandejas text NOT NULL DEFAULT '',
    siembra date NOT NULL,
    cosecha date,
    roi jsonb NOT NULL,
    escala jsonb,
    retencion_dias integer CHECK (retencion_dias BETWEEN 1 AND 3650),
    revision integer NOT NULL DEFAULT 1,
    FOREIGN KEY (vivero_id, camara_id) REFERENCES camaras(vivero_id, id),
    CHECK (cosecha IS NULL OR cosecha >= siembra)
);
CREATE INDEX IF NOT EXISTS lotes_camara ON lotes (vivero_id, camara_id, siembra);
CREATE TABLE IF NOT EXISTS notas_lote (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    lote_id bigint NOT NULL REFERENCES lotes(id),
    usuario_id bigint NOT NULL REFERENCES usuarios(id),
    fecha timestamptz NOT NULL DEFAULT now(),
    tipo text NOT NULL CHECK (tipo IN ('riego', 'luz', 'observacion', 'cosecha')),
    texto text NOT NULL
);
CREATE TABLE IF NOT EXISTS calidad (
    captura_id bigint PRIMARY KEY REFERENCES capturas(id),
    problemas text[] NOT NULL,
    brillo real NOT NULL,
    nitidez real NOT NULL,
    firma text NOT NULL,
    revision text CHECK (revision IN ('aceptada', 'descartada')),
    revisada_por bigint REFERENCES usuarios(id),
    revisada_en timestamptz
);
CREATE TABLE IF NOT EXISTS mediciones (
    captura_id bigint NOT NULL REFERENCES capturas(id),
    lote_id bigint NOT NULL REFERENCES lotes(id),
    revision integer NOT NULL,
    algoritmo text NOT NULL,
    cobertura real NOT NULL CHECK (cobertura BETWEEN 0 AND 100),
    area_cm2 real,
    mascara text NOT NULL,
    medida_en timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (captura_id, lote_id, revision)
);
CREATE INDEX IF NOT EXISTS mediciones_por_lote ON mediciones (lote_id, revision, captura_id);
CREATE TABLE IF NOT EXISTS trabajos (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    vivero_id text NOT NULL REFERENCES viveros(id),
    usuario_id bigint REFERENCES usuarios(id),
    tipo text NOT NULL CHECK (tipo IN ('analisis', 'reanalizar', 'timelapse', 'zip')),
    clave text UNIQUE,
    datos jsonb NOT NULL,
    estado text NOT NULL DEFAULT 'pendiente'
        CHECK (estado IN ('pendiente', 'en_proceso', 'listo', 'error')),
    creado_en timestamptz NOT NULL DEFAULT now(),
    disponible_en timestamptz NOT NULL DEFAULT now(),
    inicio_en timestamptz,
    fin_en timestamptz,
    intentos integer NOT NULL DEFAULT 0,
    mensaje text NOT NULL DEFAULT '',
    salida text
);
CREATE INDEX IF NOT EXISTS trabajos_pendientes ON trabajos (disponible_en, id) WHERE estado = 'pendiente';
CREATE INDEX IF NOT EXISTS trabajos_vivero ON trabajos (vivero_id, id DESC);
CREATE INDEX IF NOT EXISTS trabajos_supervisor ON trabajos (vivero_id, tipo, estado)
    INCLUDE (disponible_en, inicio_en) WHERE estado IN ('pendiente', 'en_proceso', 'error');
CREATE TABLE IF NOT EXISTS sensores (
    vivero_id text NOT NULL REFERENCES viveros(id),
    origen text NOT NULL,
    fecha timestamptz NOT NULL,
    temp_c real,
    humedad_pct real CHECK (humedad_pct BETWEEN 0 AND 100),
    lux real CHECK (lux >= 0),
    co2_ppm real CHECK (co2_ppm >= 0),
    PRIMARY KEY (vivero_id, origen, fecha)
);
CREATE INDEX IF NOT EXISTS sensores_por_sitio_fecha ON sensores (vivero_id, fecha DESC);

CREATE TABLE IF NOT EXISTS alertas (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    clave text NOT NULL,
    vivero_id text REFERENCES viveros(id),
    nivel text NOT NULL CHECK (nivel IN ('aviso', 'critica')),
    detalle text NOT NULL,
    abierta_en timestamptz NOT NULL DEFAULT now(),
    cerrada_en timestamptz,
    leida_por bigint REFERENCES usuarios(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS alertas_abiertas ON alertas (clave) WHERE cerrada_en IS NULL;
CREATE TABLE IF NOT EXISTS avisos (
    alerta_id bigint NOT NULL REFERENCES alertas(id),
    canal text NOT NULL CHECK (canal IN ('correo', 'telegram')),
    recuperada boolean NOT NULL,
    intentos integer NOT NULL DEFAULT 0,
    siguiente timestamptz NOT NULL DEFAULT now(),
    enviada_en timestamptz,
    PRIMARY KEY (alerta_id, canal, recuperada)
);

ALTER TABLE nodos ADD COLUMN IF NOT EXISTS alta_en timestamptz;
CREATE TABLE IF NOT EXISTS altas_camara (
    id text PRIMARY KEY,
    vivero_id text NOT NULL,
    nodo_id text NOT NULL,
    camara_id text,
    solicitante bigint REFERENCES usuarios(id),
    tipo text NOT NULL CHECK (tipo IN ('buscar', 'agregar')),
    nombre text NOT NULL DEFAULT '',
    hilera text NOT NULL DEFAULT '',
    estado text NOT NULL DEFAULT 'pendiente' CHECK (estado IN ('pendiente', 'en_curso', 'ok', 'error')),
    resultado jsonb NOT NULL DEFAULT '{}',
    creada_en timestamptz NOT NULL DEFAULT now(),
    vence_en timestamptz NOT NULL DEFAULT now() + interval '4 minutes',
    terminada_en timestamptz,
    CHECK ((tipo = 'agregar') = (camara_id IS NOT NULL)),
    FOREIGN KEY (vivero_id, nodo_id) REFERENCES nodos(vivero_id, id)
);
CREATE UNIQUE INDEX IF NOT EXISTS alta_camara_nodo_abierta ON altas_camara (vivero_id, nodo_id)
    WHERE estado IN ('pendiente', 'en_curso');
CREATE UNIQUE INDEX IF NOT EXISTS alta_camara_puesto_abierta ON altas_camara (vivero_id, camara_id)
    WHERE camara_id IS NOT NULL AND estado IN ('pendiente', 'en_curso');
CREATE INDEX IF NOT EXISTS alta_camara_usuario ON altas_camara (vivero_id, solicitante, creada_en DESC);

CREATE TABLE IF NOT EXISTS vinculaciones_zero (
    id text PRIMARY KEY,
    vivero_id text NOT NULL,
    nodo_id text NOT NULL,
    solicitante bigint NOT NULL REFERENCES usuarios(id),
    host text NOT NULL,
    puerto integer NOT NULL CHECK (puerto BETWEEN 1 AND 65535),
    usuario_ssh text NOT NULL,
    estado text NOT NULL DEFAULT 'pendiente' CHECK (estado IN ('pendiente', 'en_curso', 'ok', 'error')),
    mensaje text NOT NULL DEFAULT 'Preparando la conexión con la Zero.',
    token_sha char(64),
    token_previo_sha char(64),
    creada_en timestamptz NOT NULL DEFAULT now(),
    actualizada_en timestamptz NOT NULL DEFAULT now(),
    terminada_en timestamptz,
    FOREIGN KEY (vivero_id, nodo_id) REFERENCES nodos(vivero_id, id)
);
CREATE UNIQUE INDEX IF NOT EXISTS vinculacion_zero_abierta ON vinculaciones_zero (vivero_id, nodo_id)
    WHERE estado IN ('pendiente', 'en_curso');
CREATE UNIQUE INDEX IF NOT EXISTS vinculacion_host_abierta ON vinculaciones_zero (host, puerto)
    WHERE estado IN ('pendiente', 'en_curso');

ALTER TABLE altas_camara DROP CONSTRAINT IF EXISTS altas_camara_tipo_check;
ALTER TABLE altas_camara ADD CONSTRAINT altas_camara_tipo_check CHECK (tipo IN ('buscar', 'agregar', 'capturar', 'reconfigurar'));
ALTER TABLE altas_camara DROP CONSTRAINT IF EXISTS altas_camara_check;
ALTER TABLE altas_camara ADD CONSTRAINT altas_camara_check
    CHECK ((tipo IN ('agregar', 'capturar', 'reconfigurar')) = (camara_id IS NOT NULL));

CREATE TABLE IF NOT EXISTS mantenimiento (
    id text PRIMARY KEY,
    solicitante bigint REFERENCES usuarios(id),
    tipo text NOT NULL CHECK (tipo IN ('respaldo', 'verificar', 'correo', 'telegram')),
    clave text UNIQUE,
    estado text NOT NULL DEFAULT 'pendiente' CHECK (estado IN ('pendiente', 'en_curso', 'ok', 'error')),
    mensaje text NOT NULL DEFAULT 'Esperando al servicio de mantenimiento.',
    resultado jsonb NOT NULL DEFAULT '{}',
    creada_en timestamptz NOT NULL DEFAULT now(),
    inicio_en timestamptz,
    terminada_en timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS mantenimiento_tipo_abierto ON mantenimiento (tipo)
    WHERE estado IN ('pendiente', 'en_curso');

CREATE TABLE IF NOT EXISTS solicitudes_acceso (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nombre text NOT NULL,
    persona text NOT NULL,
    clave text,
    estado text NOT NULL DEFAULT 'pendiente' CHECK (estado IN ('pendiente', 'aprobada', 'rechazada')),
    creada_en timestamptz NOT NULL DEFAULT now(),
    resuelta_en timestamptz,
    resuelta_por bigint REFERENCES usuarios(id),
    cuenta_id bigint REFERENCES usuarios(id),
    CHECK ((estado = 'pendiente') = (clave IS NOT NULL)),
    CHECK ((estado = 'pendiente') = (resuelta_en IS NULL)),
    CHECK ((estado = 'pendiente') = (resuelta_por IS NULL)),
    CHECK (estado <> 'aprobada' OR cuenta_id IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS solicitud_acceso_pendiente ON solicitudes_acceso (nombre)
    WHERE estado = 'pendiente';


-- v1.0: distribución gráfica, sin modificar fotos ni credenciales existentes.
ALTER TABLE camaras ADD COLUMN IF NOT EXISTS plano_x real CHECK (plano_x BETWEEN 0.07 AND 0.93);
ALTER TABLE camaras ADD COLUMN IF NOT EXISTS plano_y real CHECK (plano_y BETWEEN 0.07 AND 0.93);
ALTER TABLE nodos ADD COLUMN IF NOT EXISTS plano_x real CHECK (plano_x BETWEEN 0.07 AND 0.93);
ALTER TABLE nodos ADD COLUMN IF NOT EXISTS plano_y real CHECK (plano_y BETWEEN 0.07 AND 0.93);
ALTER TABLE altas_camara DROP CONSTRAINT IF EXISTS altas_camara_tipo_check;
ALTER TABLE altas_camara ADD CONSTRAINT altas_camara_tipo_check CHECK (tipo IN ('buscar', 'agregar', 'capturar', 'reconfigurar'));
ALTER TABLE altas_camara DROP CONSTRAINT IF EXISTS altas_camara_check;
ALTER TABLE altas_camara ADD CONSTRAINT altas_camara_check CHECK ((tipo IN ('agregar', 'capturar', 'reconfigurar')) = (camara_id IS NOT NULL));
