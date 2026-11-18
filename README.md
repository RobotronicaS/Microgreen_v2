# MicroGreenVision Central 1.0.0

Sistema instalable para administrar invernáculos, equipos Raspberry Pi Zero y cámaras IP desde una PC. Los dibujos de los invernáculos abren su esquema interior; cada Zero y cámara se puede seleccionar para consultar su estado y abrir su configuración.

## Instalación o actualización

El servidor Central se instala en la Raspberry Pi 4/5 con Raspberry Pi OS / Debian de 64 bits. La PC Windows abre el panel en su navegador. Las Zero 2 W capturan las fotografías en cada instalación.

Si ya usás la versión 0.8.0, ejecutá el instalador con el mismo usuario Linux. Conserva `config.json`, cuentas, fotografías, historial y equipos. Guarda una copia del código, las unidades y la base de datos en `~/MicroGreenVision_Central/.versiones/` antes de actualizar.

Desde una carpeta de fuentes o desde el ZIP descomprimido:

```bash
bash instalar_central.sh
```

No anteponer `sudo`. La instalación solicita sudo para los paquetes y PostgreSQL. La primera instalación muestra una contraseña nueva de administrador; las actualizaciones conservan las cuentas existentes.

Abrí el enlace HTTPS privado de Central que ya utilizás. El servicio escucha en `127.0.0.1:8766`. [Instalación paso a paso y actualización de las Zero](docs/version_1.md).

## Recorrido principal

1. **Agregar invernáculo:** nombre y ubicación. Se genera un identificador y una Zero inicial por vincular.
2. **Abrir su dibujo:** aparecen el esquema y todos sus equipos y cámaras.
3. **Seleccionar la Zero → Conectar e instalar Zero:** ingresar la dirección, usuario Linux y acceso al equipo. También se pueden agregar más Zero desde **Agregar dispositivo**.
4. **Agregar cámara:** seleccionar su Zero, buscar por ONVIF o completar el acceso RTSP. Se prueba una fotografía antes de registrar la cámara.
5. **Seleccionar la cámara:** abrir fotos e historial, configurar su programación o cambiar su conexión.
6. **Organizar plano:** arrastrar cámaras y Zero. Las posiciones se guardan en la base de datos.

No hay un límite de 10 invernáculos ni de 15 cámaras en el catálogo. El rendimiento efectivo depende de la Raspberry, las cámaras, la frecuencia elegida, la red y el almacenamiento. Conviene distribuir las cámaras entre varios equipos Zero cuando aumenta la carga.

## Cámaras y programación

- Perfiles Tapo, Hikvision, Dahua y URL RTSP de otro fabricante.
- Cámaras con visión infrarroja cuando entregan un flujo RTSP compatible; también se reciben fotografías en blanco y negro. No es una integración genérica de sensores térmicos ni del control del iluminador IR.
- Nombre, sector/hilera, pausa y reanudación, horarios y frecuencias de 1, 2, 5, 10, 15, 30, 60, 120 o 240 minutos.
- Cambio de dirección, acceso, calidad y Zero mediante una fotografía verificada. Se conserva la configuración previa cuando la prueba falla.
- TCP o UDP; giro de 90°, 180° o 270°; ancho original o máximo de 640/1280/1920 px; espera de 5 a 60 segundos.
- Las opciones nuevas y frecuencias menores a 15 minutos requieren actualizar la Zero a 1.0 desde su ficha.

Los códigos de cámara aceptan hasta 32 letras, números, guiones o guiones bajos. Son únicos dentro de cada invernáculo y permanecen estables para conservar el historial.

## Funciones conservadas

Captura manual, cola local ante desconexiones, recepción con acuse y verificación, historial por fecha, descarga de originales, ZIP, comparación y timelapse, lotes de cultivo, regiones de análisis y cobertura vegetal, sensores, supervisor, avisos, respaldos y verificación de copias.

El panel muestra fotografías guardadas y su fecha; no es una transmisión de video en vivo. El análisis de cobertura no equivale a una medición universal de altura.

## Usuarios

- **Consulta:** ve los invernáculos autorizados y sus fotografías.
- **Técnico:** administra cámaras, programación y posiciones del plano en sus invernáculos.
- **Administrador:** administra invernáculos, Zero, personas y mantenimiento.

Los nuevos registros se aprueban desde **Configuración → Administración**. [Registro de usuarios](docs/registro.md).

## Documentación

- [Instalar, actualizar y comenzar con la versión 1.0](docs/version_1.md).
- [Conexiones y preparación de los equipos](docs/instalacion.md).
- [Operación, avisos y respaldos](docs/operacion.md).
- [Guía del docente](docs/docente.md).
- [Cambios de versión](CHANGELOG.md).

## Comprobación del software

Las pruebas usan PostgreSQL aislado y una Zero simulada para comprobar permisos, migraciones, captura y reintentos, 150 cámaras, altas fuera de la plantilla, reconfiguración y posiciones persistentes. No contactan cámaras ni envían avisos externos.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r pruebas/requirements.txt
PYTHONPATH=. .venv/bin/python -m pytest -q pruebas
```

La prueba de respaldos completos requiere `rsync`; las integraciones necesitan PostgreSQL. No ejecutar las pruebas con la base de producción. Los instaladores de servicios están destinados a Linux con systemd.
