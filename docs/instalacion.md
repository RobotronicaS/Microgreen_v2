> Para la interfaz ilustrada y las opciones de la versión 1.0, empezá por [Instalación y uso de Central 1.0](version_1.md). Los pasos siguientes conservan la documentación de las versiones anteriores.

# Instalación de Microgreen2 desde cero

Seguí esta guía si te asignaron instalar Central y la Zero. Si Central ya está instalado y querés entrar con una dirección fija, empezá por [configurar el acceso privado](#3-configurá-el-acceso-privado-a-central). Si ya tenés el enlace del panel y una Zero vinculada, empezá por la [práctica](../README.md).

## 1. Reuní los datos y abrí PowerShell

- **Central** es un equipo con Debian 13 o Raspberry Pi OS basado en Debian 13, systemd, conexión a Internet y una cuenta Linux con permiso para usar sudo.
- **Zero** es una Raspberry Pi Zero 2 W con Raspberry Pi OS, acceso SSH y una cuenta con sudo. Conectala al Wi‑Fi que comparte con la cámara. Para una tarjeta nueva, elegí el equipo y el sistema en Raspberry Pi Imager. Configurá nombre de usuario, Wi‑Fi y SSH antes de grabar la tarjeta. [Guía de Raspberry Pi Imager y SSH](https://www.raspberrypi.com/documentation/computers/getting-started.html)
- Anotá el usuario y la IP de cada equipo en la forma **usuario@IP**. Si todavía no sabés la IP, buscá el nombre del equipo en la lista de dispositivos del router.
- En Windows, abrí **Inicio → PowerShell**. Escribí `ssh -V`. Si Windows no encuentra el comando, activá **Cliente OpenSSH** en **Configuración → Características opcionales** y volvé a abrir PowerShell.
- Desde el repositorio **Microgreen_v2** en GitHub, abrí **Code → Download ZIP**. Guardá el archivo en **Descargas** con el nombre **Microgreen_v2-main.zip**. [Descargar código desde GitHub](https://docs.github.com/es/repositories/working-with-files/using-files/downloading-source-code-archives)

| Pantalla | Dónde pegar el bloque |
| --- | --- |
| Prompt que empieza con `PS C:\` | PowerShell en Windows. |
| Prompt como `usuario@central:~ $` | Terminal SSH de Central. |
| Prompt como `usuario@zero:~ $` | Terminal SSH de la Zero. |

Al escribir la contraseña de SSH o sudo, la terminal mantiene el cursor quieto. Escribila y pulsá **Enter**. La primera conexión SSH puede mostrar la huella del equipo. Confirmala con el docente o en la consola del equipo antes de aceptar.

## 2. Copiá el ZIP e instalá Central

Pegá este bloque en **PowerShell**. Cuando te pregunte la cuenta, escribí el **usuario@IP de Central** y pulsá Enter.

~~~powershell
$zip = Join-Path $env:USERPROFILE 'Downloads\Microgreen_v2-main.zip'
$central = Read-Host 'Usuario@IP de Central'
scp.exe $zip "${central}:Microgreen_v2-main.zip"
if ($LASTEXITCODE -eq 0) {
    ssh.exe $central
}
~~~

Pegá el siguiente bloque cuando ya estés **dentro de Central**. El instalador prepara PostgreSQL, crea el catálogo y arranca el panel.

~~~bash
sudo apt update &&
sudo apt install -y unzip curl ca-certificates &&
mkdir -p "$HOME/mgv-fuentes" &&
unzip -o "$HOME/Microgreen_v2-main.zip" -d "$HOME/mgv-fuentes" &&
cd "$HOME/mgv-fuentes/Microgreen_v2-main" &&
bash ./instalar_central.sh
~~~

En la primera instalación, anotá la contraseña que aparece en **Primer acceso al panel**. Comprobá que el servicio esté activo desde **Central**.

~~~bash
curl -fsS http://127.0.0.1:8766/salud
~~~

El resultado esperado es `{"estado":"ok","version":"0.8.0"}`. Central crea **Vivero piloto** con el nodo **zero01** y el puesto **H1-C01**. Guardá la contraseña de la instalación para el primer acceso por HTTPS.

## 3. Configurá el acceso privado a Central

**El acceso habitual es un favorito del navegador.** Tailscale Serve mantiene una dirección HTTPS en Central para el docente, el estudiante autorizado y las Zero. La configuración se hace una vez en Central.

Si Central todavía necesita Tailscale, ejecutá estos comandos en su terminal y seguí el enlace de ingreso que imprime. [Instalar Tailscale en Linux](https://tailscale.com/docs/install/linux)

~~~bash
sudo apt install -y curl ca-certificates &&
curl -fsSLo "$HOME/instalar-tailscale.sh" https://tailscale.com/install.sh &&
sh "$HOME/instalar-tailscale.sh" &&
sudo tailscale up
~~~

En **Central**, ejecutá `tailscale serve status`. Si todavía falta el servicio HTTPS de Microgreen2, ejecutá lo siguiente.

~~~bash
sudo tailscale serve --bg --https=8766 http://127.0.0.1:8766
tailscale serve status
~~~

Si aparece un enlace para habilitar HTTPS en la red, seguí ese enlace y repetí el comando. Copiá la dirección HTTPS que figura como **Available within your tailnet**, con el puerto **8766**. La opción `--bg` mantiene el acceso activo al cerrar SSH y lo vuelve a levantar cuando reinicia el servidor. [Tailscale Serve](https://tailscale.com/docs/reference/tailscale-cli/serve)

1. En tu computadora o celular, conectá Tailscale y abrí esa dirección en el navegador. El acceso debe estar autorizado en tu red de Tailscale.
2. Ingresá con tu cuenta de Central. En el primer acceso de una instalación nueva, usá **admin** y la contraseña que imprimió el instalador. Las actualizaciones conservan la contraseña anterior. En **Mi cuenta** podés cambiarla.
3. Guardá el enlace como **MicroGreenVision Central** en favoritos. Usá la misma dirección en el campo `url` de cada Zero vinculada a este servidor y autorizá su acceso al puerto **8766** en Tailscale.

Para comprobar el enlace, agregá `/salud` a la dirección HTTPS. El navegador debería mostrar `{"estado":"ok","version":"0.8.0"}`.

### Recuperá el acceso de admin

Si olvidaste la contraseña de `admin`, ejecutá estos comandos en **PowerShell**.

~~~powershell
$central = Read-Host 'Usuario@IP de Central'
ssh.exe -t $central 'cd ~/MicroGreenVision_Central && /usr/bin/python3 -m mgv_central clave-admin'
~~~

Ingresá la contraseña SSH del usuario de Central. El comando genera una nueva contraseña para `admin`, la muestra en la terminal y cierra sus sesiones abiertas. Copiá la nueva contraseña e ingresá al panel con `admin`. Podés cambiarla desde **Mi cuenta**.

## 4. Vinculá la Zero desde la página

La Zero debe estar encendida, conectada a Tailscale y tener SSH habilitado. Su usuario Linux debe tener permiso sudo.

1. Abrí Central con el enlace HTTPS e ingresá como **admin**.
2. Abrí **Equipos Zero** y buscá tu equipo. Para otra Raspberry, pulsá **Agregar Zero**, elegí el invernáculo, escribí un nombre y pulsá **Crear equipo**.
3. En la ficha, pulsá **Vincular Zero**. Si el equipo ya estaba vinculado, el botón se llama **Configurar Zero**.
4. Escribí la IP de Tailscale o el nombre de la Raspberry, su usuario Linux y la contraseña de ese usuario.
5. Pulsá **Conectar e instalar**. Central copia el programa, prepara la credencial y comprueba el enlace. La página muestra el progreso y permite reintentar si ocurre un error.
6. Cuando aparezca **Zero vinculada y lista para agregar cámaras**, pulsá **Agregar cámara**. Continuá con la [búsqueda de cámaras](../README.md#2-buscá-la-cámara).

La dirección de Central se completa con el enlace desde el que abriste el panel. Podés revisarla en **Opciones de conexión**, junto al puerto SSH. Central usa la contraseña durante la instalación y guarda la identidad SSH del equipo para las próximas conexiones. Los registros de vinculación guardan el estado y los mensajes del procedimiento.

## Instalación manual de la Zero

En el panel, con la cuenta **admin**, abrí **Equipos Zero**. Buscá **Vivero piloto** y el equipo **zero01**. Para otra Raspberry, pulsá **Agregar Zero**, elegí su invernáculo, poné un nombre y creá el equipo. En la ficha correspondiente pulsá **Vincular Zero** y copiá el JSON. Si la Zero ya está vinculada a ese mismo nodo, usá su `central.json` actual. **Renovar credencial** cambia el acceso del equipo. Usá esa opción si necesitás reemplazar su credencial.

Abrí **otra ventana de PowerShell**. Escribí el **usuario@IP de la Zero** cuando te lo pida.

~~~powershell
$zip = Join-Path $env:USERPROFILE 'Downloads\Microgreen_v2-main.zip'
$zero = Read-Host 'Usuario@IP de la Zero'
scp.exe $zip "${zero}:Microgreen_v2-main.zip"
if ($LASTEXITCODE -eq 0) {
    ssh.exe $zero
}
~~~

Pegá este bloque dentro de **la Zero**.

~~~bash
sudo apt update &&
sudo apt install -y unzip nano &&
mkdir -p "$HOME/mgv-fuentes" &&
unzip -o "$HOME/Microgreen_v2-main.zip" -d "$HOME/mgv-fuentes"
~~~

Ejecutá **tailscale status** en la Zero y verificá que aparezca Central. Si falta Tailscale en la Zero, ejecutá en esta ventana el bloque de instalación de Tailscale del paso 3. Al abrir el enlace de ingreso, usá la misma red privada que Central.

Si es el primer enlace, abrí el archivo para la credencial dentro de **la Zero**.

~~~bash
umask 077
mkdir -p "$HOME/MicroGreenVision_Zero"
nano "$HOME/MicroGreenVision_Zero/central.json"
~~~

Pegá el JSON en **nano** y reemplazá el valor de `"url"` por la URL HTTPS que copiaste. Conservá las comillas y el resto de los campos. Guardá con **Ctrl+O**, confirmá con **Enter** y salí con **Ctrl+X**. La credencial se usa en la Zero y se guarda fuera del repositorio.

### Instalá el agente y probá una cámara

Ejecutá en **la Zero**.

~~~bash
cd "$HOME/mgv-fuentes/Microgreen_v2-main" &&
bash zero/instalar_zero.sh &&
systemctl --user is-active microgreen-zero-central-altas.timer
~~~

El instalador prepara una Zero nueva con una configuración de cámaras vacía. También prueba que su credencial sirva antes de activar los temporizadores. El último comando debería mostrar **active**. Esperá unos segundos y actualizá **Equipos Zero**. Cuando tu equipo diga **Lista para agregar cámaras**, pulsá **Agregar cámara**. Seguí desde el [paso de búsqueda de la práctica](../README.md#2-buscá-la-cámara).

Si la Zero sigue sin enlace, consultá el registro desde su terminal.

~~~bash
journalctl --user -u microgreen-zero-central-altas.service -n 25 --no-pager
~~~

## Diagnóstico por SSH

Si necesitás comprobar el panel local desde Windows durante la configuración de HTTPS, podés abrir un túnel temporal desde PowerShell. Mientras esté abierto, el panel queda disponible en `http://127.0.0.1:8766`.

~~~powershell
$central = Read-Host 'Usuario@IP de Central'
ssh.exe -N -o ExitOnForwardFailure=yes -L 127.0.0.1:8766:127.0.0.1:8766 $central
~~~

