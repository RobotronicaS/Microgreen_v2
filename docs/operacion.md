# Operación de MicroGreenVision Central v0.8.0

Abrí el enlace HTTPS habitual con Tailscale conectado. Central conserva las cuentas y las cámaras al actualizarse. La instalación se comprueba en `/salud`, que debe mostrar `{"estado":"ok","version":"0.8.0"}`.

## 1. Revisá el inicio

La primera pantalla reúne los avisos abiertos, los invernáculos que requieren atención y las cámaras al día. **Revisar estado** abre el supervisor. **Equipos y cámaras** abre las Raspberry Pi Zero y sus puestos.

El encabezado muestra la versión activa del programa que está ejecutando Central. El mismo valor aparece en `/salud`.

El administrador también tiene los accesos **Agregar compañero** y **Avisos y respaldos**. Cada compañero debe usar su propia cuenta. Los visitantes pueden pulsar **Registrarse** en la pantalla de ingreso. Desde **Administración → Registros pendientes** autorizás sus invernáculos con acceso de Consulta. La [guía de registro](registro.md) explica los pasos.

## 2. Habilitá la captura manual

Después de actualizar Central, actualizá la Zero una vez.

1. Abrí **Equipos Zero** y buscá tu Raspberry.
2. Pulsá **Configurar Zero**.
3. Ingresá su dirección de Tailscale, su usuario Linux y la contraseña de ese usuario.
4. Pulsá **Conectar e instalar** y esperá a que termine.
5. Abrí una cámara con **Ver historial**, actualizá la página y pulsá **Tomar foto ahora**.
6. Esperá la miniatura con el mensaje **Captura recibida en Central**. La entrega y la incorporación al historial pueden demorar unos ciclos de los temporizadores.

La captura usa la configuración local de esa cámara en la Zero. Las cuentas con permiso Técnico pueden solicitarla. Las cuentas de Consulta pueden ver el historial.

Si el botón está deshabilitado, comprobá que la Zero esté conectada y actualizada. Si aparece un error de cámara, revisá su alimentación y su red local. Si la foto queda pendiente de recepción, consultá **Supervisor** y el estado de envío de esa Zero.

## 3. Configurá los avisos

Ingresá como administrador y abrí **Avisos y respaldos**.

### Correo

1. Pedí al responsable de la cuenta de correo los datos de SMTP y, si corresponde, una contraseña de aplicación.
2. Completá servidor, puerto, tipo de conexión, usuario y contraseña.
3. Escribí el remitente y los destinatarios. Separá varios destinatarios con comas.
4. Marcá **Enviar avisos por correo** y pulsá **Guardar avisos**.
5. Pulsá **Probar correo**. Esperá el resultado en **Operaciones recientes** y comprobá la llegada del mensaje.

### Telegram

1. Tené a mano el token del bot y el identificador del chat que recibirá los avisos. El bot debe tener permiso para enviar mensajes a ese chat.
2. Completá ambos campos y marcá **Enviar avisos por Telegram**.
3. Pulsá **Guardar avisos** y luego **Probar Telegram**.
4. Esperá el resultado en **Operaciones recientes** y comprobá el mensaje en el chat.

Al volver a abrir la página, las credenciales aparecen vacías con la indicación de que están guardadas. Dejalas vacías para conservarlas o escribí nuevas para reemplazarlas. Los canales habilitados reciben los incidentes nuevos y las alertas que todavía están abiertas. También se avisa cuando una alerta se cierra. Al corregir la configuración de un canal, se reintentan sus envíos pendientes.

## 4. Prepará y verificá el respaldo

1. Conectá el disco que vas a usar y comprobá su montaje y el permiso de escritura del usuario de Central.
2. En **Carpeta de destino en Central**, escribí una ruta absoluta separada de las capturas. Un ejemplo es `/mnt/respaldo/microgreenvision`, si ese es el disco que preparaste.
3. Para una primera prueba también podés usar `/home/tu_usuario/microgreen-respaldos`, reemplazando `tu_usuario` por tu usuario Linux. Esa copia ocupa el mismo almacenamiento que el sistema. Para recuperarte de una falla del disco, prepará un destino en otro dispositivo.
4. Marcá la programación diaria si la querés activar y pulsá **Guardar respaldo**. La copia está programada a las **02.20, hora del servidor**. Si estaba apagado, el temporizador puede ejecutarla al volver a encenderlo.
5. Pulsá **Crear respaldo ahora**. Podés seguir usando el panel mientras se procesa.
6. Cuando termine, pulsá **Verificar último respaldo**. El resultado debe indicar que la base es legible y cuántos archivos se comprobaron por SHA-256.

Cada copia completa guarda la base de datos, las capturas y sus archivos derivados, la configuración de Central y las opciones de avisos y respaldo que existan. La carpeta también contiene credenciales y debe quedar bajo acceso del administrador. Los respaldos anteriores se conservan. Revisá el espacio disponible en el destino.

La verificación del panel lee el volcado de PostgreSQL y compara las huellas de los archivos. Una recuperación completa también requiere restaurar el volcado en otra base y comprobar sus capturas. Las copias anteriores sin manifiesto se identifican como una verificación parcial.

## 5. Comprobación técnica aislada

Las pruebas crean su propio PostgreSQL temporal y usan fotos de ejemplo. Simulan la captura de la Zero, una interrupción, el envío de avisos, la recepción de 150 cámaras y una restauración en otra base. No contactan cámaras ni envían avisos reales.

Desde una terminal Linux con los paquetes de Central instalados, ejecutá en la carpeta del código.

```bash
sudo apt install -y python3-pytest python3-httpx
PYTHONPATH="$PWD" /usr/bin/python3 -m pytest -q pruebas
```

Ejecutá las pruebas con tu usuario Linux habitual. Si PostgreSQL está instalado en otra ubicación, indicá su carpeta de binarios mediante `MGV_TEST_PG_BIN`. Una prueba omitida por falta de dependencias no cuenta como una comprobación aprobada.

El ensayo de 150 cámaras verifica el programa con cargas simuladas. Antes de habilitar los diez invernáculos, probá un invernáculo completo y medí tiempos de captura, envío, almacenamiento y uso de CPU con el hardware y la red reales.
