"use strict";

const formularioZero = document.querySelector("[data-vincular-zero]");
if (formularioZero) {
  formularioZero.addEventListener("submit", () => {
    const boton = formularioZero.querySelector('button[type="submit"]');
    boton.disabled = true;
    boton.textContent = "Conectando…";
  });
}

const vinculacion = document.querySelector("[data-vinculacion]");
if (vinculacion) {
  const mensaje = vinculacion.querySelector("[data-vinculacion-mensaje]");
  const lista = vinculacion.querySelector("[data-vinculacion-lista]");
  const reintentar = vinculacion.querySelector("[data-vinculacion-reintentar]");

  async function actualizarVinculacion() {
    try {
      const respuesta = await fetch("/api/vinculaciones-zero/" + encodeURIComponent(vinculacion.dataset.vinculacion),
        {headers: {Accept: "application/json"}, cache: "no-store"});
      if (respuesta.redirected || respuesta.status === 401 || respuesta.status === 403) {
        mensaje.textContent = "Volvé a ingresar a Central para consultar la instalación.";
        return;
      }
      if (!respuesta.ok) throw new Error("Estado pendiente de consulta");
      const estado = await respuesta.json();
      mensaje.textContent = estado.mensaje;
      lista.hidden = estado.estado !== "ok";
      reintentar.hidden = estado.estado !== "error";
      if (estado.estado === "ok" || estado.estado === "error") return;
    } catch {
      mensaje.textContent = "Esperando conexión con Central. La consulta se reintentará automáticamente.";
    }
    window.setTimeout(actualizarVinculacion, 2000);
  }

  actualizarVinculacion();
}
