"use strict";

for (const formulario of document.querySelectorAll("[data-operacion-form]")) {
  formulario.addEventListener("submit", () => {
    // El navegador incluye el botón pulsado en el envío del formulario.
    window.setTimeout(() => {
      for (const boton of formulario.querySelectorAll("button")) boton.disabled = true;
    }, 0);
  });
}

const operacion = document.querySelector("[data-operacion]");
if (operacion && ["pendiente", "en_curso"].includes(operacion.dataset.estado)) {
  async function actualizarOperacion() {
    try {
      const respuesta = await fetch("/api/mantenimiento/" + encodeURIComponent(operacion.dataset.operacion),
        {headers: {Accept: "application/json"}, cache: "no-store"});
      if (respuesta.redirected || respuesta.status === 403) {
        operacion.textContent = "Volvé a ingresar como administrador para consultar la operación.";
        return;
      }
      if (!respuesta.ok) throw new Error("Consulta pendiente");
      const estado = await respuesta.json();
      operacion.textContent = estado.mensaje;
      if (estado.estado === "ok" || estado.estado === "error") {
        location.reload();
        return;
      }
    } catch {
      operacion.textContent = "Esperando conexión con Central. La consulta se reintentará automáticamente.";
    }
    window.setTimeout(actualizarOperacion, 2500);
  }
  actualizarOperacion();
}
