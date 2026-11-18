# Instalación y uso de Central 1.0.0

## 1. Dónde se instala

Central se instala en la Raspberry Pi que ya tiene el servidor. La PC se usa para abrir el navegador y administrar los invernáculos. Cada Raspberry Pi Zero 2 W trabaja como equipo de captura cerca de sus cámaras.

La instalación necesita Internet para los paquetes, un usuario Linux con sudo, PostgreSQL y systemd. El script prepara los paquetes requeridos. Conservá la red Tailscale y el enlace HTTPS privado que ya están en uso.

## 2. Actualizar desde la 0.8.0

1. Ingresá por SSH a la Raspberry de Central con el mismo usuario de la instalación anterior.
2. Descargá o cloná esta versión en una carpeta nueva. No ejecutes el instalador desde `~/MicroGreenVision_Central`.
3. Desde la carpeta descargada, ejecutá:

```bash
bash instalar_central.sh
```

El instalador detiene los servicios, respalda la base, copia el programa, aplica la migración y reinicia los servicios. En una actualización conserva usuarios, claves, invernáculos, equipos, cámaras, fotos, colas y configuración local. La carpeta de reserva se imprime al terminar. No es necesario borrar la instalación anterior.

Comprobá:

```bash
curl http://127.0.0.1:8766/salud
systemctl --user status microgreen-central-panel.service --no-pager
```

La respuesta de salud debe incluir `"estado":"ok"` y `"version":"1.0.0"`. Abrí el mismo enlace HTTPS de antes y recargá la página. La nueva versión debe aparecer en el encabezado. Una instalación nueva muestra su primera clave de administrador en la terminal; la actualización conserva la cuenta existente.

Si se interrumpe, el instalador intenta reponer el código previo y deja su reserva. No borres `.versiones`: conserva el volcado previo de PostgreSQL. La restauración completa de esa base se hace con los servicios detenidos y revisando primero la copia correspondiente; no alcanza con reemplazar archivos Python para revertir cambios de datos posteriores.

## 3. Actualizar cada Zero

Actualizar Central no actualiza automáticamente todas las Zero.

1. Abrí el dibujo del invernáculo.
2. Seleccioná una Zero.
3. Pulsá **Configurar o actualizar Zero**.
4. Completá dirección IP o nombre Tailscale, usuario y contraseña Linux.
5. En opciones, verificá la URL HTTPS de Central y el puerto SSH.
6. Pulsá **Conectar e instalar** y esperá la confirmación.
7. Volvé al esquema y actualizá la página tras el próximo informe. La ficha debe mostrar agente **1.0.0**.

El instalador guarda el motor anterior en `.versiones` de la Zero y conserva `config.json`, `central.json`, fotos pendientes y credenciales de las cámaras. Actualiza el motor de captura, no solo el agente. La identidad de una Zero continúa vinculada a un único equipo de un invernáculo.

Los agentes anteriores siguen usando la captura programada existente. Para reconfigurar conexiones, usar giro/tamaño/transporte o seleccionar frecuencias menores a 15 minutos, primero actualizá su agente. Central rechaza los cambios nuevos cuando la Zero no informa la capacidad necesaria.

## 4. Agregar invernáculos y equipos

En la página inicial pulsá **Agregar invernáculo**, completá nombre y ubicación y guardá. La zona horaria habitual es America/Montevideo. Se genera el código interno y una Zero inicial por vincular; no se crean fotos ni cámaras de muestra.

Abrí el dibujo. Para una Zero adicional elegí **Agregar dispositivo → Raspberry Pi Zero**, escribí su nombre y continuá a la conexión. Repetí el proceso para cada equipo. Un técnico puede configurar cámaras; agregar equipos y administrar invernáculos requiere administrador.

No hay un cupo fijo de invernáculos ni de cámaras por plantilla. Una sola Zero captura de forma secuencial: un intervalo corto combinado con muchas cámaras o conexiones lentas puede generar atrasos. Verificá el supervisor antes de aumentar la carga; las 150 cámaras de la prueba son distribuidas en distintos invernáculos y no representan una medición física de una única Zero.

## 5. Agregar una cámara

1. Dentro del esquema, seleccioná su Zero y **Agregar cámara**.
2. Elegí o escribí el código único de la cámara. Los puestos sugeridos son ejemplos; podés escribir otros códigos, como CAM016 o FONDO01.
3. Completá nombre y hilera/ubicación.
4. Podés buscar por ONVIF en la red local de esa Zero. Si no aparece, ingresá la IP manualmente.
5. Elegí Tapo, Hikvision, Dahua o una URL RTSP completa. En equipos multicanal elegí el canal correcto.
6. Completá el acceso local de la cámara. En Tapo se usa la cuenta de cámara habilitada en su aplicación.
7. En **Opciones de imagen y conexión** podés configurar TCP/UDP, giro, ancho del JPG y tiempo de espera.
8. Pulsá **Probar y agregar**. Se toma una fotografía desde la Zero y se registra el puesto únicamente si la prueba se confirma.
9. Abrí el historial para verificar fecha y encuadre. La recepción y la incorporación a Central pueden tardar otro ciclo.

La cámara debe ser accesible en la red local de la Zero; no alcanza con que tenga una app o sea Wi-Fi. Un equipo con iluminador infrarrojo funciona si ofrece RTSP. El modo IR automático, exposición, enfoque y otros controles del fabricante se ajustan desde el dispositivo. Los perfiles predefinidos deben coincidir con el modelo y su configuración; una URL RTSP personalizada permite usar otras rutas.

## 6. Configurar una cámara existente

Seleccioná la cámara → **Configurar cámara**. Cambiá nombre, hilera, frecuencia, horas y **Captura activa**. Desde y Hasta iguales significa las 24 horas. La pausa conserva las fotografías; la Zero recibe la programación al sincronizar, y mientras esté desconectada conserva su política anterior.

Para cambiar contraseña, IP, ruta de video, orientación, calidad o trasladarla a otra Zero del mismo invernáculo, pulsá **Cambiar conexión o calidad**. Completá los nuevos datos y probá. Si falla la nueva fotografía, se conserva el acceso local anterior. Si cambiás la Zero, el equipo nuevo debe poder llegar a la cámara; la asignación cambia después de confirmar la prueba. Se conserva el código y el historial de la cámara.

No se trasladan archivos entre Zero: las capturas nuevas se incorporan en Central. El equipo anterior deja de programar esa cámara al sincronizar su política; las fotos pendientes previas pueden terminar de enviarse por su autorización histórica.

## 7. Organizar el esquema

Pulsá **Organizar plano** y arrastrá una Zero o una cámara. Al soltar se guarda la ubicación y se actualiza el plano. Las líneas se generan según el equipo asignado a cada cámara. Es una distribución orientativa sin escala física; mover un dibujo no cambia la red ni la asignación del equipo.

## 8. Fotografías, análisis y operación

Seleccioná una cámara → **Ver fotos e historial**. Allí se encuentran la captura manual, filtros por fecha, comparación, selección para ZIP, descarga de originales y timelapse. La generación de ZIP o video aparece en **Configuración → Salud del sistema → Procesamiento**. La captura manual requiere una Zero en línea y no convierte el panel en video en vivo.

Cultivos y sensores permanecen dentro de cada invernáculo. Los procesos de análisis y los datos de sensores requieren las fuentes y calibraciones que correspondan. No se inventan lecturas cuando no hay un sensor conectado.

En **Configuración** se reúnen Equipos Zero, Supervisor, Procesamiento, Administración y Avisos y respaldos. Los permisos se siguen aplicando en el servidor, aunque alguien intente abrir una URL directamente.

## 9. Verificación de esta entrega

Se probaron las operaciones con PostgreSQL temporal, altas y reconfiguraciones con una Zero simulada, permisos y migraciones, 10 invernáculos/150 cámaras y la expansión a códigos fuera de los 15 puestos. En el navegador se comprobó crear un invernáculo, seleccionar varias Zero, guardar la configuración y arrastrar un dispositivo con recarga y posición persistente.

La instalación del servicio en la Raspberry física, la calidad de cada cámara y los tiempos de captura en tu red requieren una comprobación después de instalar. La versión no supone una validación física de todos los modelos comerciales.
