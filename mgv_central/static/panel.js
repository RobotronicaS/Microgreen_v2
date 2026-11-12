"use strict";

for (const boton of document.querySelectorAll("[data-volver]")) {
  boton.addEventListener("click", () => history.back());
}
for (const boton of document.querySelectorAll("[data-imprimir]")) {
  boton.addEventListener("click", () => window.print());
}
for (const formulario of document.querySelectorAll("[data-confirmar]")) {
  formulario.addEventListener("submit", (evento) => {
    if (!confirm(formulario.dataset.confirmar)) evento.preventDefault();
  });
}
for (const boton of document.querySelectorAll("[data-copiar]")) {
  boton.addEventListener("click", async () => {
    const campo = document.getElementById(boton.dataset.copiar);
    campo.focus();
    campo.select();
    try {
      await navigator.clipboard.writeText(campo.value);
      boton.textContent = "Copiado";
    } catch {
      boton.textContent = "Seleccionado para copiar";
    }
  });
}

const fotos = [...document.querySelectorAll('input[name="fotos"][form="seleccion"]')];
const contador = document.querySelector("[data-contador]");
if (contador) {
  const actualizar = () => {
    contador.textContent = fotos.filter((foto) => foto.checked).length + " seleccionadas";
  };
  for (const foto of fotos) foto.addEventListener("change", actualizar);
  actualizar();
}

const comparador = document.querySelector("[data-comparador]");
const deslizador = document.querySelector("[data-deslizador]");
if (comparador && deslizador) {
  deslizador.addEventListener("input", () => comparador.style.setProperty("--corte", deslizador.value + "%"));
}

const roi = document.querySelector("[data-roi]");
if (roi) {
  const form = roi.closest("form");
  const campos = ["x", "y", "ancho", "alto"].map((nombre) => form.elements[nombre]);
  const marco = roi.querySelector(".roi-marco");
  let inicio = null;
  const limites = () => {
    const [x, y, ancho, alto] = campos.map((campo) => Number(campo.value));
    Object.assign(marco.style, {left: x + "%", top: y + "%", width: ancho + "%", height: alto + "%"});
  };
  const punto = (evento) => {
    const caja = roi.getBoundingClientRect();
    const ajustar = (n) => Math.max(0, Math.min(100, Math.round(n * 10) / 10));
    return [ajustar(100 * (evento.clientX - caja.left) / caja.width),
      ajustar(100 * (evento.clientY - caja.top) / caja.height)];
  };
  roi.addEventListener("pointerdown", (evento) => {
    if (evento.button !== 0) return;
    inicio = punto(evento);
    roi.setPointerCapture(evento.pointerId);
    evento.preventDefault();
  });
  roi.addEventListener("pointermove", (evento) => {
    if (!inicio) return;
    const final = punto(evento);
    const x = Math.min(99, inicio[0], final[0]);
    const y = Math.min(99, inicio[1], final[1]);
    [x, y, Math.max(1, Math.max(inicio[0], final[0]) - x),
      Math.max(1, Math.max(inicio[1], final[1]) - y)].forEach((valor, i) => campos[i].value = valor.toFixed(1));
    limites();
  });
  roi.addEventListener("pointerup", () => inicio = null);
  roi.addEventListener("pointercancel", () => inicio = null);
  for (const campo of campos) campo.addEventListener("input", limites);
  const camara = form.querySelector("[data-camara-roi]");
  camara.addEventListener("change", () => {
    const datos = camara.selectedOptions[0].dataset;
    roi.hidden = !datos.foto;
    form.querySelector("[data-sin-foto]").hidden = !!datos.foto;
    if (datos.foto) roi.querySelector("img").src = "/miniaturas/" + datos.foto + ".jpg";
    form.elements.escala_ancho.placeholder = datos.ancho;
    form.elements.escala_alto.placeholder = datos.alto;
  });
  limites();
}

function dibujarGrafica(canvas) {
  const datos = JSON.parse(canvas.dataset.puntos);
  const rect = canvas.getBoundingClientRect();
  if (!datos.length || rect.width < 50) return;
  const escala = window.devicePixelRatio || 1;
  canvas.width = Math.round(rect.width * escala);
  canvas.height = Math.round(rect.height * escala);
  const contexto = canvas.getContext("2d");
  contexto.scale(escala, escala);
  const margen = 40;
  const ancho = rect.width - margen - 12;
  const alto = rect.height - 35;
  const valores = datos.map((punto) => punto[1]);
  const base = canvas.dataset.maximo ? 0 : Math.min(...valores);
  const tope = Math.max(base + 1, Number(canvas.dataset.maximo) || Math.max(...valores));
  const y = (valor) => 7 + (tope - valor) * alto / (tope - base);
  const tiempos = datos.map((punto) => Date.parse(punto[0]));
  const tiempoValido = tiempos.every(Number.isFinite);
  const distancia = Math.max(1, tiempos.at(-1) - tiempos[0]);
  const x = (i) => margen + (datos.length === 1 ? ancho / 2 :
    ancho * (tiempoValido ? (tiempos[i] - tiempos[0]) / distancia : i / (datos.length - 1)));
  contexto.font = "11px Segoe UI, sans-serif";
  contexto.fillStyle = "#51665a";
  contexto.strokeStyle = "#d7e2d9";
  for (let marca = 0; marca <= 4; marca++) {
    const valor = base + marca * (tope - base) / 4;
    contexto.fillText(Number(valor.toFixed(1)).toString(), 1, y(valor) + 4);
    contexto.beginPath();
    contexto.moveTo(margen, y(valor));
    contexto.lineTo(margen + ancho, y(valor));
    contexto.stroke();
  }
  contexto.fillText(datos[0][0].slice(0, 10), margen, rect.height - 5);
  if (datos.length > 1 && ancho > 145) {
    contexto.textAlign = "right";
    contexto.fillText(datos.at(-1)[0].slice(0, 10), margen + ancho, rect.height - 5);
  }
  contexto.strokeStyle = "#186747";
  contexto.lineWidth = 2;
  contexto.beginPath();
  valores.forEach((valor, i) => i === 0 ? contexto.moveTo(x(i), y(valor)) : contexto.lineTo(x(i), y(valor)));
  contexto.stroke();
  contexto.fillStyle = "#186747";
  if (valores.length < 30) {
    valores.forEach((valor, i) => {
      contexto.beginPath();
      contexto.arc(x(i), y(valor), 3, 0, Math.PI * 2);
      contexto.fill();
    });
  }
}
for (const canvas of document.querySelectorAll("[data-puntos]")) {
  new ResizeObserver(() => dibujarGrafica(canvas)).observe(canvas);
}
