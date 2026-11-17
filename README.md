# Microgreen2

Microgreen2 usa **MicroGreenVision Central v0.5** y una Raspberry Pi Zero para tomar fotos de un invernáculo. En esta práctica vas a agregar **una cámara**, tomar su primera foto y verla en el historial.

| Equipo | Qué hace |
| --- | --- |
| Central | Muestra el panel, recibe las fotos y guarda el historial. |
| Raspberry Pi Zero | Busca la cámara en su red local, toma el JPG y lo envía a Central. |
| Tu computadora o celular | Abre el enlace HTTPS de Central con Tailscale conectado. |

## Antes de empezar

GitHub guarda el código y esta guía. Para trabajar con la cámara, abrí el panel de Central.

Tené a mano el enlace del panel, tu usuario y contraseña, y el nombre del invernáculo y puesto que te asignó el docente. Usá la cámara de la práctica y conectala a la misma red local que la Zero. Para el alta vas a necesitar la cuenta local de la cámara o su URL RTSP.

Si te toca instalar los equipos, seguí la [instalación desde cero](docs/instalacion.md). La [guía del docente](docs/docente.md) explica cómo darte acceso al repositorio y preparar tu cuenta.

## 1. Entrá a tu invernáculo

1. Conectá Tailscale, abrí el enlace HTTPS que te dio el docente y guardalo en favoritos.
2. Ingresá con tu cuenta del panel. El permiso **Técnico** habilita el alta en tu invernáculo.
3. Abrí **Equipos Zero**, buscá el invernáculo y el equipo que te asignaron y pulsá **Agregar cámara** en su ficha.

## 2. Buscá la cámara

1. Confirmá que **Zero de captura** muestra tu equipo. Un nombre de ejemplo es **zero01**.
2. Pulsá **Buscar cámaras**. La Zero consulta por ONVIF en su Wi‑Fi. Esperá a que aparezcan los resultados.
3. Ubicá tu cámara por nombre o por IP y pulsá **Usar IP**. Si la búsqueda queda vacía, ingresá la IP de tu cámara. Podés encontrarla en su app o en la lista de equipos conectados del router.

## 3. Completá los datos

| Campo del panel | Qué poner |
| --- | --- |
| **Puesto** | El código asignado. El primer puesto suele ser `H1-C01`. Si el docente ya preparó uno que figura **Esperando JPG**, elegí ese. |
| **Nombre (opcional)** | Una referencia que puedas reconocer, por ejemplo **Entrada de la hilera**. |
| **Tipo de cámara** | **Tapo**, **Hikvision** u **Otra cámara RTSP**. |
| **IP de la cámara** | La IPv4 que detectó la Zero o que encontraste en el router. Ejemplo de formato `192.168.1.42`. |
| **Puerto RTSP** | Dejá `554` si la cámara usa el puerto habitual. Usá el que configuraron en la cámara si es otro. |
| **Usuario y contraseña de cámara** | Para Tapo, usá la **Cuenta de cámara** creada en su app. Para Hikvision, usá un usuario de ese equipo con permiso para ver el video. |
| **Calidad de captura** | Empezá con **Estándar**. |
| **URL RTSP de la cámara** | Aparece al elegir **Otra cámara RTSP**. Pegá la URL del fabricante con la IP de tu cámara. El formato es `rtsp://usuario:clave@IP:554/ruta`. |

Para ver cómo se prepara la cuenta o el perfil, consultá las guías de [Tapo](https://www.tp-link.com/es/support/faq/2680/) y [Hikvision](https://supportusa.hikvision.com/support/solutions/articles/17000129022-do-you-have-an-example-showing-the-format-for-getting-a-rtsp-stream-from-a-camera-).

## 4. Probá y agregá la cámara

1. Pulsá **Probar y agregar** una vez y dejá la página abierta. Central le pide a la Zero que tome un JPG.
2. Si la prueba funciona, se registra el puesto. El panel puede mostrar **Esperando el primer JPG en Central** mientras se incorpora la foto.
3. Cuando aparezca la miniatura, abrí **Ver historial**. Verificá que el código del puesto y la fecha correspondan a tu prueba.

La práctica queda completa cuando podés mostrar **el invernáculo, el puesto y una foto en el historial**. Anotá la hora de esa captura.

## Si te trabás

| Lo que ves | Qué hacer |
| --- | --- |
| Falta **Agregar cámara** | Confirmá que tu cuenta tenga permiso **Técnico** en ese invernáculo y que tu Zero figure como **Lista para agregar cámaras**. |
| La Zero dice **sin enlace de alta** | Avisale al docente qué nodo aparece. Puede revisar el agente y el enlace HTTPS desde la Zero. |
| **Buscar cámaras** no encuentra equipos | Confirmá la IP en el router. Cargala a mano y probá. |
| La prueba rechaza el acceso | Revisá la cuenta local de la cámara y que RTSP esté habilitado. Volvé a pulsar **Probar y agregar**. |
| La cámara quedó registrada y la foto todavía no aparece | Abrí **Ver historial** después del ciclo de importación. Si sigue vacío, pasale al docente el puesto y el mensaje de la prueba para revisar **Supervisor**. |
