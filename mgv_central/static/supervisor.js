(() => {
  const panel = document.querySelector('[data-supervisor]');
  if (!panel) return;

  const actualizarEnlace = document.querySelector('[data-supervisor-actualizar]');
  const mensaje = panel.querySelector('[data-supervisor-mensaje]');
  const error = mensaje.querySelector('[data-supervisor-error]');
  const ingreso = mensaje.querySelector('[data-supervisor-ingreso]');
  const intervalo = 30_000;
  let ultimaLectura = Date.now();
  let actualizando = false;
  let sesionVencida = false;

  function mostrarEstado(texto = '') {
    error.textContent = texto;
    mensaje.hidden = !texto;
    ingreso.hidden = !sesionVencida;
    panel.toggleAttribute('data-supervisor-desactualizado', Boolean(texto));
  }

  function reemplazarLectura(nueva) {
    const anterior = panel.querySelector('[data-supervisor-datos]');
    const secciones = new Map();
    for (const seccion of anterior.querySelectorAll('[data-supervisor-seccion]')) {
      secciones.set(seccion.dataset.supervisorSeccion, seccion);
    }
    let foco;
    for (const seccion of nueva.querySelectorAll('[data-supervisor-seccion]')) {
      const previa = secciones.get(seccion.dataset.supervisorSeccion);
      if (!previa) continue;
      seccion.open = previa.open;
      if (previa.querySelector('summary') === document.activeElement) foco = seccion.querySelector('summary');
    }
    anterior.replaceWith(nueva);
    foco?.focus({ preventScroll: true });
  }

  async function actualizar() {
    if (actualizando || document.hidden || sesionVencida) return;
    actualizando = true;
    actualizarEnlace.setAttribute('aria-busy', 'true');
    const control = new AbortController();
    const limite = window.setTimeout(() => control.abort(), 8000);
    try {
      const respuesta = await fetch('/supervisor?parcial=true', { cache: 'no-store', signal: control.signal });
      if (respuesta.status === 401 || (respuesta.redirected && new URL(respuesta.url).pathname === '/ingresar')) {
        sesionVencida = true;
        mostrarEstado('La sesión requiere ingreso. Se muestra la última lectura.');
        return;
      }
      if (!respuesta.ok) {
        mostrarEstado('Central no pudo actualizar el Supervisor. Se muestra la última lectura.');
        return;
      }
      const documento = new DOMParser().parseFromString(await respuesta.text(), 'text/html');
      const nueva = documento.querySelector('[data-supervisor-datos]');
      if (!nueva) throw new Error('Lectura incompleta');
      reemplazarLectura(nueva);
      ultimaLectura = Date.now();
      mostrarEstado();
    } catch {
      mostrarEstado('No se pudo actualizar desde Central. Se muestra la última lectura.');
    } finally {
      window.clearTimeout(limite);
      actualizarEnlace.removeAttribute('aria-busy');
      actualizando = false;
    }
  }

  actualizarEnlace.addEventListener('click', (evento) => {
    if (sesionVencida || evento.ctrlKey || evento.metaKey || evento.shiftKey || evento.altKey) return;
    evento.preventDefault();
    actualizar();
  });
  window.setInterval(actualizar, intervalo);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && Date.now() - ultimaLectura >= intervalo && !sesionVencida) {
      mostrarEstado('Actualizando los datos de Central.');
      actualizar();
    }
  });
  window.addEventListener('online', actualizar);
  window.addEventListener('offline', () => {
    if (!sesionVencida) mostrarEstado('El navegador perdió la conexión. Se muestra la última lectura.');
  });
})();
