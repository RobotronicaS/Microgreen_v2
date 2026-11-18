> Para la interfaz ilustrada y las opciones de la versión 1.0, empezá por [Instalación y uso de Central 1.0](version_1.md). Los pasos siguientes conservan la documentación de las versiones anteriores.

# Microgreen2 para el docente

La práctica usa **un estudiante, una cuenta de tipo Técnico, una Zero y una cámara**. Podés usar el invernáculo que ya tenés preparado y asignarle un puesto disponible.


## 1. Dale acceso al repositorio

La práctica está publicada en el repositorio privado [RobotronicaS/Microgreen_v2](https://github.com/RobotronicaS/Microgreen_v2).

Para compartir un repositorio privado, entrá a **Settings → Collaborators → Add people** y buscá el usuario de GitHub del estudiante. Una vez que acepte la invitación, pasale el enlace al README. [Invitar a un colaborador](https://docs.github.com/es/repositories/managing-your-repositorys-settings-and-features/repository-access-and-collaboration/inviting-collaborators-to-a-personal-repository)

## 2. Prepará la Zero y su cámara

1. Entrá a **Equipos Zero**. Si el equipo todavía no figura, elegí **Agregar Zero**, seleccioná el invernáculo y escribí un nombre. Central asigna un ID como **zero02**.
2. En la ficha nueva, pulsá **Vincular Zero**. Completá su dirección, usuario y contraseña Linux y pulsá **Conectar e instalar**. Seguí los [pasos de vinculación](instalacion.md#4-vinculá-la-zero-desde-la-página).
3. Actualizá el panel. Cuando la Zero figure **Lista para agregar cámaras**, abrí **Agregar cámara** desde su ficha. Los puestos vacíos de la plantilla también están disponibles. Se asignan a esta Zero cuando la prueba toma su primera foto.

Para actualizar un Central existente, descargá el ZIP actual y ejecutá de nuevo [el instalador de Central](instalacion.md#2-copiá-el-zip-e-instalá-central). Los equipos enlazados con agentes anteriores siguen enviando sus capturas. Para habilitar **Tomar foto ahora**, actualizá cada Zero desde **Equipos Zero → Configurar Zero → Conectar e instalar**.

## 3. Prepará su cuenta en Central

Verificá que Central abra con su [enlace privado de Tailscale](instalacion.md#3-configurá-el-acceso-privado-a-central). En el panel, ingresá como administrador.

Si la persona sólo va a visualizar, pedile que use **Registrarse** en la pantalla de ingreso. Luego autorizá sus invernáculos desde **Administración → Registros pendientes**. Esa cuenta recibe permiso de Consulta. Para la práctica de agregar cámaras, prepará el permiso Técnico con los pasos siguientes.

1. Abrí **Administración → Nueva cuenta**. Creá una cuenta para el estudiante, por ejemplo **estudiante**, con una contraseña inicial de al menos 12 caracteres. Dejá desmarcada la opción **Administrador de todos los sitios**.
2. En **Permisos por sitio**, elegí su cuenta y el invernáculo asignado. Seleccioná **Técnico** y pulsá **Guardar acceso**.
3. Confirmá que en **Equipos Zero** la Raspberry esté lista para agregar cámaras y que el puesto elegido esté libre. Si el agente o el servidor todavía requieren instalación, seguí la [instalación desde cero](instalacion.md).
4. Autorizá al estudiante a llegar a Central por Tailscale. Compartile en privado el enlace HTTPS, su cuenta de Central y la contraseña inicial. Indicá el invernáculo, la Zero, el puesto y la cámara que tiene que usar.

El estudiante completa la guía del README y muestra la primera foto de su puesto en **Ver historial**.

## 4. Prepará los avisos y respaldos

En **Avisos y respaldos**, el administrador puede guardar y probar los canales de notificación, elegir la carpeta de respaldo y activar la copia diaria. Seguí la [guía de operación](operacion.md) para comprobar la primera copia y su integridad.

## Verificación técnica opcional

El docente puede ejecutar la prueba automatizada desde **Central**, con el servicio y PostgreSQL ya preparados. Usa una base temporal, genera fotos de prueba y verifica permisos y altas. El estudiante puede completar la práctica con una sola cámara.

La simulación de 150 cámaras comprueba el funcionamiento del programa. La capacidad del servidor y de la red se debe medir con las cámaras y las frecuencias de captura de la instalación real.

~~~bash
sudo apt install -y python3-httpx
cd "$HOME/mgv-fuentes/Microgreen_v2-main" &&
MGV_CENTRAL_CONFIG="$HOME/MicroGreenVision_Central/config.json" \
PYTHONPATH="$PWD" /usr/bin/python3 pruebas/carga_150.py
~~~

