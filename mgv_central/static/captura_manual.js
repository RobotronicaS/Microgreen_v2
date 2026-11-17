"use strict";

const controlCaptura = document.querySelector("[data-captura-manual]");
if (controlCaptura) {
  const formulario = controlCaptura.querySelector("form");
  const boton = formulario.querySelector("button");
  const mensaje = controlCaptura.querySelector("[data-captura-mensaje]");
  const imagen = controlCaptura.querySelector("[data-captura-imagen]");
  const historial = controlCaptura.querySelector("[data-captura-historial]");
  const habilitado = !boton.disabled;
  let inicio = Date.now();

  async function consultar(solicitud) {
    try {
      const respuesta = await fetch("/api/viveros/" + encodeURIComponent(controlCaptura.dataset.vivero) + "/altas/" + encodeURIComponent(solicitud),
        {headers: {Accept: "application/json"}, cache: "no-store"});
      if (respuesta.redirected) throw new Error("Volvé a ingresar a Central.");
      const estado = await respuesta.json();
      if (!respuesta.ok) throw new Error(estado.detalle || "Revisá la conexión con Central.");
      if (estado.estado === "error") throw new Error(estado.mensaje || "La Zero no pudo completar la captura.");
      if (estado.estado === "ok") {
        historial.href = estado.historial;
        historial.hidden = false;
        if (estado.miniatura) {
          imagen.src = estado.miniatura;
          imagen.hidden = false;
          mensaje.textContent = "Captura recibida en Central.";
          boton.disabled = !habilitado;
          return;
        }
        mensaje.textContent = "La Zero tomó la foto. Esperando su incorporación a Central.";
      } else {
        mensaje.textContent = "La Zero está preparando la captura.";
      }
    } catch (error) {
      mensaje.textContent = error.message;
      boton.disabled = !habilitado;
      return;
    }
    if (Date.now() - inicio > 5 * 60 * 1000) {
      mensaje.textContent = "Consultá el historial y el estado de la Zero para comprobar la entrega de la foto.";
      boton.disabled = !habilitado;
      return;
    }
    window.setTimeout(() => consultar(solicitud), 2000);
  }

  formulario.addEventListener("submit", async (evento) => {
    evento.preventDefault();
    boton.disabled = true;
    imagen.hidden = true;
    historial.hidden = true;
    mensaje.textContent = "Solicitando una captura a la Zero.";
    try {
      const respuesta = await fetch(formulario.action, {method: "POST", headers: {Accept: "application/json"}, body: new URLSearchParams(new FormData(formulario))});
      if (respuesta.redirected) throw new Error("Volvé a ingresar a Central.");
      const estado = await respuesta.json();
      if (!respuesta.ok) throw new Error(estado.detalle || "La captura no pudo solicitarse.");
      inicio = Date.now();
      const url = new URL(location.href);
      url.searchParams.set("captura", estado.id);
      history.replaceState(null, "", url);
      consultar(estado.id);
    } catch (error) {
      mensaje.textContent = error.message;
      boton.disabled = !habilitado;
    }
  });
  if (controlCaptura.dataset.solicitud) {
    boton.disabled = true;
    consultar(controlCaptura.dataset.solicitud);
  }
}
