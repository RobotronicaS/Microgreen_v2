# Instalación de Microgreen2 desde cero

Seguí esta guía si te asignaron instalar Central y la Zero. Si el docente te dio un panel y una Zero ya vinculados, empezá directamente por la [práctica](../README.md).

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
    ssh.exe -o ExitOnForwardFailure=yes -L 127.0.0.1:8766:127.0.0.1:8766 $central
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

El resultado esperado es `{"estado":"ok","version":"0.4"}`. Dejá abierta la ventana SSH y entrá al [panel de Central](http://127.0.0.1:8766). Iniciá sesión con **admin** y la contraseña de la instalación. En **Mi cuenta** podés cambiarla. Central crea **Vivero piloto** con el nodo **zero01** y el puesto **H1-C01**.

## 3. Prepará el enlace privado de la Zero a Central

Para el alta remota, Central y la Zero tienen que estar en la misma red privada de Tailscale. Si los equipos ya figuran en esa red, usá sus accesos actuales. Si Central todavía no tiene Tailscale, ejecutá estos comandos en la terminal de Central y seguí el enlace de ingreso que imprime. El administrador de la red tiene que permitir que la Zero llegue al puerto **8766** de Central. [Instalar Tailscale en Linux](https://tailscale.com/docs/install/linux)

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

Si aparece un enlace para habilitar HTTPS en la red, seguí ese enlace y repetí el comando. Copiá la URL HTTPS completa de Central, incluido el puerto que muestre. [Tailscale Serve](https://tailscale.com/docs/reference/tailscale-cli/serve)

## 4. Copiá el ZIP y la credencial a la Zero

En el panel, con la cuenta **admin**, abrí **Vivero piloto → Configurar cámaras**. En el nodo **zero01**, pulsá **Generar credencial** y copiá el JSON. Si el docente preparó otro invernáculo o nodo para la práctica, seleccioná ese. Si la Zero ya está vinculada a ese mismo nodo, usá su `central.json` actual. Generar otra credencial para un nodo enlazado reemplaza su acceso anterior.

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

## 5. Instalá el agente y probá una cámara

Ejecutá en **la Zero**.

~~~bash
cd "$HOME/mgv-fuentes/Microgreen_v2-main" &&
bash zero/instalar_zero.sh &&
systemctl --user is-active microgreen-zero-central-altas.timer
~~~

El instalador prepara una Zero nueva con una configuración de cámaras vacía. También prueba que su credencial sirva antes de activar los temporizadores. El último comando debería mostrar **active**. Esperá unos segundos y actualizá **Vivero piloto → Agregar cámara**. La Zero debería estar disponible. Seguí desde el [paso de búsqueda de la práctica](../README.md#2-buscá-la-cámara).

Si la Zero sigue sin enlace, consultá el registro desde su terminal.

~~~bash
journalctl --user -u microgreen-zero-central-altas.service -n 25 --no-pager
~~~

Si cerraste la sesión de Central y querés volver al panel local, abrí el túnel desde **PowerShell** y mantené la ventana abierta.

~~~powershell
$central = Read-Host 'Usuario@IP de Central'
ssh.exe -N -o ExitOnForwardFailure=yes -L 127.0.0.1:8766:127.0.0.1:8766 $central
~~~
