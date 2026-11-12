# Microgreen2 para el docente

La práctica usa **un estudiante, una cuenta de tipo Técnico, una Zero y una cámara**. Podés usar el invernáculo que ya tenés preparado y asignarle un puesto disponible.


## 1. Dale acceso al repositorio

La práctica está publicada en el repositorio privado [RobotronicaS/Microgreen_v2](https://github.com/RobotronicaS/Microgreen_v2).

Para compartir un repositorio privado, entrá a **Settings → Collaborators → Add people** y buscá el usuario de GitHub del estudiante. Una vez que acepte la invitación, pasale el enlace al README. [Invitar a un colaborador](https://docs.github.com/es/repositories/managing-your-repositorys-settings-and-features/repository-access-and-collaboration/inviting-collaborators-to-a-personal-repository)

## 2. Prepará su cuenta en Central

El acceso al código y el acceso al panel se crean por separado. En **Central**, ingresá como administrador.

1. Abrí **Administración → Nueva cuenta**. Creá una cuenta para el estudiante, por ejemplo **estudiante**, con una contraseña inicial de al menos 12 caracteres. Dejá desmarcada la opción **Administrador de todos los sitios**.
2. En **Permisos por sitio**, elegí su cuenta y el invernáculo asignado. Seleccioná **Técnico** y pulsá **Guardar acceso**.
3. Confirmá que **Agregar cámara** muestre una Zero disponible y que el puesto elegido esté libre. Si el agente o el servidor todavía requieren instalación, seguí la [instalación desde cero](instalacion.md).
4. Compartí en privado el enlace del panel, la cuenta y la contraseña inicial. Indicá el invernáculo, la Zero, el puesto y la cámara que tiene que usar. Si el panel está servido por Tailscale, autorizá el acceso del estudiante a Central en la red de la práctica.

El estudiante completa la guía del README y muestra la primera foto de su puesto en **Ver historial**.

## Verificación técnica opcional

El docente puede ejecutar la prueba automatizada desde **Central**, con el servicio y PostgreSQL ya preparados. Usa una base temporal, genera fotos de prueba y verifica permisos y altas. El estudiante puede completar la práctica con una sola cámara.

~~~bash
sudo apt install -y python3-httpx
cd "$HOME/mgv-fuentes/Microgreen_v2-main" &&
MGV_CENTRAL_CONFIG="$HOME/MicroGreenVision_Central/config.json" \
PYTHONPATH="$PWD" /usr/bin/python3 pruebas/carga_150.py
~~~
