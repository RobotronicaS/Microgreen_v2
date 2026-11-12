(() => {
  const formulario = document.querySelector('[data-alta]');
  if (!formulario) return;

  const campo = nombre => formulario.elements.namedItem(nombre);
  const elemento = nombre => formulario.querySelector('[data-alta-' + nombre + ']');
  const nodo = campo('nodo');
  const puesto = campo('camara');
  const buscar = elemento('buscar');
  const agregar = elemento('guardar');
  const resultados = elemento('equipos');
  const estado = elemento('resultado');
  const mensaje = elemento('mensaje');
  const foto = elemento('foto');
  const historial = elemento('historial');
  const otra = elemento('otra');
  const ocupados = new Set();
  let solicitud = formulario.dataset.solicitud;
  let enCurso = Boolean(solicitud);
  let terminada = false;
  let consultasFoto = 0;

  function mostrar(texto, nivel = '') {
    mensaje.textContent = texto;
    estado.dataset.nivel = nivel;
    estado.hidden = false;
  }

  function actualizarCampos() {
    const disponible = nodo.selectedOptions[0]?.dataset.disponible === 'si';
    for (const opcion of puesto.options) {
      opcion.disabled = opcion.dataset.nodo !== nodo.value || ocupados.has(opcion.value);
      opcion.hidden = opcion.disabled;
    }
    if (!puesto.selectedOptions[0] || puesto.selectedOptions[0].disabled) {
      const libre = Array.from(puesto.options).find(opcion => !opcion.disabled);
      puesto.selectedIndex = libre ? libre.index : -1;
    }
    const esRtsp = campo('tipo').value === 'rtsp';
    elemento('perfil').hidden = elemento('perfil').disabled = esRtsp;
    elemento('rtsp').hidden = elemento('rtsp').disabled = !esRtsp;
    elemento('tapo').hidden = campo('tipo').value !== 'tapo';
    elemento('enlace').hidden = disponible;
    elemento('sin-puestos').hidden = Boolean(puesto.value) || !disponible;
    nodo.disabled = enCurso;
    puesto.disabled = enCurso;
    buscar.disabled = enCurso || terminada || !disponible;
    agregar.disabled = enCurso || terminada || !disponible || !puesto.value;
  }

  function recordar(id) {
    solicitud = id;
    const url = new URL(window.location.href);
    if (id) url.searchParams.set('solicitud', id);
    else url.searchParams.delete('solicitud');
    window.history.replaceState(null, '', url);
  }

  async function leer(url, opciones) {
    const control = new AbortController();
    const limite = window.setTimeout(() => control.abort(), 8000);
    try {
      return await leerRespuesta(await fetch(url, { cache: 'no-store', signal: control.signal, ...opciones }));
    } finally {
      window.clearTimeout(limite);
    }
  }

  async function leerRespuesta(respuesta) {
    if (respuesta.redirected || respuesta.status === 401) {
      const error = new Error('Volvé a ingresar a Central para continuar.');
      error.sesion = true;
      throw error;
    }
    const datos = await respuesta.json();
    if (!respuesta.ok) {
      const error = new Error(typeof datos.detalle === 'string' ? datos.detalle : 'Central no pudo completar la solicitud.');
      error.codigo = respuesta.status;
      throw error;
    }
    return datos;
  }

  function mostrarEquipos(equipos) {
    resultados.replaceChildren();
    for (const equipo of equipos) {
      const fila = document.createElement('li');
      const texto = document.createElement('span');
      texto.textContent = equipo.nombre + ' · ' + equipo.ip;
      const usar = document.createElement('button');
      usar.type = 'button';
      usar.className = 'boton';
      usar.textContent = 'Usar IP';
      usar.addEventListener('click', () => {
        campo('ip').value = equipo.ip;
        if (campo('tipo').value === 'rtsp') {
          campo('rtsp').placeholder = 'rtsp://usuario:clave@' + equipo.ip + ':554/ruta';
          campo('rtsp').focus();
        } else {
          campo('usuario_camara').focus();
        }
      });
      fila.append(texto, usar);
      resultados.append(fila);
    }
    resultados.hidden = equipos.length === 0;
  }

  function terminar() {
    enCurso = false;
    actualizarCampos();
  }

  async function consultar() {
    try {
      const alta = await leer(formulario.dataset.api + '/' + solicitud);
      if (alta.nodo !== nodo.value && Array.from(nodo.options).some(opcion => opcion.value === alta.nodo)) {
        nodo.value = alta.nodo;
        actualizarCampos();
      }
      if (alta.estado === 'error') {
        mostrar(alta.mensaje, 'error');
        recordar('');
        terminar();
        return;
      }
      if (alta.estado === 'pendiente') {
        mostrar('Esperando respuesta de la Zero…');
      } else if (alta.estado === 'en_curso') {
        mostrar(alta.tipo === 'buscar' ? 'La Zero está buscando cámaras…' : 'La Zero está probando el acceso y tomando un JPG…');
      } else if (alta.tipo === 'buscar') {
        mostrarEquipos(alta.camaras);
        mostrar(alta.camaras.length
          ? 'Elegí Usar IP junto a tu cámara.'
          : 'No aparecieron equipos ONVIF. Podés ingresar la IP de la cámara.');
        recordar('');
        terminar();
        return;
      } else {
        historial.href = alta.historial;
        historial.hidden = false;
        mostrar('Puesto ' + alta.camara + ' registrado. Esperando el primer JPG en Central…');
        consultasFoto += 1;
        if (alta.miniatura || consultasFoto >= 48) {
          if (alta.miniatura) {
            foto.src = alta.miniatura;
            foto.hidden = false;
            mostrar('Cámara ' + alta.camara + ' agregada. Central recibió la primera foto.', 'ok');
          } else {
            mostrar('Puesto ' + alta.camara + ' registrado. Podés seguir la llegada del primer JPG en el historial.');
          }
          terminada = true;
          ocupados.add(alta.camara);
          otra.hidden = false;
          terminar();
          return;
        }
      }
    } catch (error) {
      mostrar(error.sesion ? error.message : 'Central no pudo actualizar la prueba. Se reintentará.', 'error');
      if (error.sesion || error.codigo === 404 || error.codigo === 403) {
        if (!error.sesion) mostrar(error.message, 'error');
        if (!error.sesion) recordar('');
        terminar();
        return;
      }
    }
    window.setTimeout(consultar, 2500);
  }

  async function solicitar(accion) {
    if (enCurso || (accion === 'agregar' && agregar.disabled)) return;
    const datos = accion === 'buscar' ? new URLSearchParams({
      csrf: campo('csrf').value,
      nodo: nodo.value,
    }) : new URLSearchParams(new FormData(formulario));
    datos.set('accion', accion);
    enCurso = true;
    consultasFoto = 0;
    historial.hidden = foto.hidden = otra.hidden = true;
    if (accion === 'buscar') mostrarEquipos([]);
    actualizarCampos();
    mostrar('Enviando la solicitud a la Zero…');
    try {
      const alta = await leer(formulario.dataset.api, { method: 'POST', body: datos });
      recordar(alta.id);
      if (accion === 'agregar') {
        campo('clave_camara').value = '';
        campo('rtsp').value = '';
      }
      consultar();
    } catch (error) {
      const sinRespuesta = error instanceof SyntaxError || error instanceof TypeError || error.name === 'AbortError';
      mostrar(sinRespuesta ? 'Central no respondi\u00f3. Recarg\u00e1 la p\u00e1gina para retomar.' : error.message, 'error');
      terminar();
    }
  }

  nodo.addEventListener('change', () => {
    mostrarEquipos([]);
    actualizarCampos();
  });
  campo('tipo').addEventListener('change', actualizarCampos);
  formulario.addEventListener('submit', evento => {
    evento.preventDefault();
    solicitar('agregar');
  });
  buscar.addEventListener('click', () => solicitar('buscar'));
  otra.addEventListener('click', () => {
    recordar('');
    terminada = false;
    estado.hidden = true;
    campo('nombre').value = campo('ip').value = '';
    actualizarCampos();
    puesto.focus();
  });
  actualizarCampos();
  if (solicitud) consultar();
})();
