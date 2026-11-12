"use strict";

const porId = (id) => document.getElementById(id);
const formulario = porId("formulario");
const registro = porId("registro");
const campo = (nombre) => formulario.elements.namedItem(nombre);
const grupos = new Map();
const tarjetas = new Map();
const formatoFecha = new Intl.DateTimeFormat("es-UY", {dateStyle: "short", timeStyle: "short"});
const estados = {
  "sin configurar": "Sin configurar",
  pendiente: "Sin probar",
  capturando: "Capturando",
  ok: "OK",
  antigua: "Foto antigua",
  error: "Error",
  pausada: "Pausada",
  interrumpida: "Interrumpida",
};

let datosActuales;
let consultando = false;
let solicitando = false;

function aviso(mensaje, error = false) {
  const elemento = porId("aviso");
  elemento.textContent = mensaje;
  elemento.hidden = !mensaje;
  elemento.toggleAttribute("data-error", error);
}

function grupo(id) {
  const coincidencia = /^H([123])-C\d+$/i.exec(id);
  const titulo = coincidencia ? "Hilera " + coincidencia[1] : "Otras cámaras";
  if (!grupos.has(titulo)) {
    const seccion = document.createElement("section");
    const encabezado = document.createElement("h2");
    encabezado.textContent = titulo;
    const grilla = document.createElement("div");
    seccion.className = "hilera";
    grilla.className = "grilla";
    seccion.append(encabezado, grilla);
    porId("hileras").append(seccion);
    grupos.set(titulo, grilla);
  }
  return grupos.get(titulo);
}

function actualizarControles() {
  const ocupado = solicitando || !datosActuales || datosActuales.ocupado;
  porId("probar-todas").disabled = ocupado || !datosActuales.camaras.some((cam) => cam.configurada);
  porId("registrar").disabled = ocupado;
  for (const tarjeta of tarjetas.values()) {
    tarjeta.probar.disabled = ocupado;
    tarjeta.editar.disabled = ocupado;
  }
}

function mostrarCamara(cam) {
  let tarjeta = tarjetas.get(cam.id);
  if (!tarjeta) {
    const nodo = porId("tarjeta").content.firstElementChild.cloneNode(true);
    tarjeta = {
      nodo,
      puesto: nodo.querySelector(".puesto"),
      ubicacion: nodo.querySelector(".ubicacion"),
      estado: nodo.querySelector(".estado"),
      fecha: nodo.querySelector(".fecha"),
      detalle: nodo.querySelector(".detalle"),
      foto: nodo.querySelector("img"),
      vacia: nodo.querySelector(".sin-foto"),
      probar: nodo.querySelector(".probar"),
      editar: nodo.querySelector(".editar"),
    };
    tarjeta.puesto.textContent = cam.id;
    tarjeta.probar.setAttribute("aria-label", "Probar " + cam.id);
    tarjeta.probar.addEventListener("click", () => capturar(cam.id));
    tarjeta.editar.addEventListener("click", () => abrirRegistro(cam.id));
    tarjetas.set(cam.id, tarjeta);
    grupo(cam.id).append(nodo);
  }
  const fecha = cam.hora_foto && new Date(cam.hora_foto);
  const antigua = cam.estado === "ok" && fecha && Date.now() - fecha.getTime() > 45 * 60000;
  const estado = antigua ? "antigua" : cam.estado;
  tarjeta.estado.textContent = estados[estado] || estado;
  tarjeta.estado.dataset.estado = estado;
  tarjeta.ubicacion.textContent = [cam.nombre, cam.host].filter(Boolean).join(" · ");
  tarjeta.ubicacion.hidden = !tarjeta.ubicacion.textContent;
  tarjeta.fecha.textContent = fecha ? "Último JPG · " + formatoFecha.format(fecha) : "";
  tarjeta.fecha.hidden = !fecha;
  tarjeta.detalle.textContent = cam.detalle;
  tarjeta.detalle.hidden = !["error", "pausada", "interrumpida"].includes(cam.estado);
  tarjeta.probar.hidden = !cam.configurada;
  tarjeta.editar.textContent = cam.configurada ? "Editar" : "Registrar";
  tarjeta.editar.setAttribute("aria-label", tarjeta.editar.textContent + " " + cam.id);
  tarjeta.foto.hidden = !cam.foto;
  tarjeta.vacia.hidden = Boolean(cam.foto);
  tarjeta.vacia.textContent = cam.configurada ? "Sin captura" : "Puesto disponible";
  if (cam.foto && tarjeta.foto.dataset.version !== cam.foto) {
    tarjeta.foto.dataset.version = cam.foto;
    tarjeta.foto.alt = "Última captura de " + (cam.nombre || cam.id);
    tarjeta.foto.src = "/foto/" + encodeURIComponent(cam.id) + ".jpg?v=" + encodeURIComponent(cam.foto);
  }
  if (!cam.foto) {
    tarjeta.foto.removeAttribute("src");
    delete tarjeta.foto.dataset.version;
  }
}

function mostrarEstado(datos) {
  datosActuales = datos;
  for (const cam of datos.camaras) mostrarCamara(cam);
  const ids = new Set(datos.camaras.map((cam) => cam.id));
  for (const [id, tarjeta] of tarjetas) {
    if (!ids.has(id)) {
      tarjeta.nodo.remove();
      tarjetas.delete(id);
    }
  }
  porId("total").textContent = datos.camaras.filter((cam) => cam.configurada).length;
  porId("correctas").textContent = datos.camaras.filter((cam) => cam.estado === "ok").length;
  porId("pendientes").textContent = datos.pendientes;
  porId("envio").textContent = datos.envio.detalle || "Pendiente de primera prueba";
  const ciclo = datos.ciclo;
  const progreso = porId("progreso");
  progreso.hidden = !datos.ocupado;
  if (datos.ocupado) {
    progreso.textContent = datos.envio.estado === "enviando"
      ? datos.envio.detalle
      : ciclo.estado === "en curso" && ciclo.total
        ? "Cámaras comprobadas: " + ciclo.hechas + " de " + ciclo.total
        : "Operación en curso";
  }
  const conexion = porId("conexion");
  conexion.textContent = datos.mensaje || "Zero conectada";
  conexion.toggleAttribute("data-error", Boolean(datos.mensaje));
  actualizarControles();
}

async function refrescar() {
  if (consultando) return;
  consultando = true;
  try {
    const respuesta = await fetch("/api/estado", {cache: "no-store", signal: AbortSignal.timeout(7000)});
    if (!respuesta.ok) throw new Error("Panel no disponible");
    mostrarEstado(await respuesta.json());
  } catch {
    porId("conexion").textContent = "Panel sin conexión · revisá el túnel SSH";
    porId("conexion").setAttribute("data-error", "");
  } finally {
    consultando = false;
  }
}

async function enviar(ruta, datos = {}) {
  let respuesta;
  try {
    respuesta = await fetch(ruta, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(datos),
      signal: AbortSignal.timeout(12000),
    });
  } catch {
    throw new Error("No hay respuesta de la Zero. Revisá el túnel SSH.");
  }
  const resultado = await respuesta.json();
  if (!respuesta.ok) throw new Error(resultado.error || "No se pudo completar la operación.");
  return resultado;
}

async function capturar(id) {
  solicitando = true;
  actualizarControles();
  aviso("");
  try {
    const resultado = await enviar("/api/capturar", id ? {id} : {});
    aviso(resultado.mensaje);
  } catch (error) {
    aviso(error.message, true);
  } finally {
    await refrescar();
    solicitando = false;
    actualizarControles();
  }
}

function mostrarTipo() {
  const tapo = campo("tipo").value === "tapo";
  porId("opciones-tapo").hidden = !tapo;
  porId("opciones-tapo").disabled = !tapo;
  porId("opciones-rtsp").hidden = tapo;
  porId("opciones-rtsp").disabled = tapo;
  campo("ip").required = tapo;
}

function abrirRegistro(id, candidata) {
  const camaras = datosActuales?.camaras || [];
  const cam = camaras.find((fila) => fila.id === id)
    || camaras.find((fila) => candidata && fila.host === candidata.ip)
    || camaras.find((fila) => !fila.configurada);
  formulario.reset();
  const opciones = camaras.map((fila) => {
    const opcion = document.createElement("option");
    opcion.value = fila.id;
    return opcion;
  });
  porId("puestos").replaceChildren(...opciones);
  campo("id").value = cam?.id || "";
  campo("id").readOnly = Boolean(id && cam?.configurada);
  campo("nombre").value = cam?.nombre || "";
  campo("tipo").value = cam?.configurada ? cam.tipo : "tapo";
  campo("ip").value = candidata?.ip || cam?.host || "";
  campo("perfil").value = cam?.perfil || "stream2";
  porId("titulo-registro").textContent = cam?.configurada ? "Editar cámara" : "Registrar cámara";
  porId("ayuda-tapo").textContent = cam?.configurada
    ? "Podés dejar en blanco el usuario y la contraseña para conservar la cuenta guardada."
    : "Usá la Cuenta de cámara de la app Tapo.";
  porId("ayuda-rtsp").textContent = cam?.configurada
    ? "Dejá la URL en blanco para conservar la guardada."
    : "Usá la URL RTSP que indica el fabricante para ese modelo.";
  porId("error-registro").hidden = true;
  mostrarTipo();
  registro.showModal();
}

formulario.addEventListener("submit", async (evento) => {
  evento.preventDefault();
  porId("guardar").disabled = true;
  porId("error-registro").hidden = true;
  try {
    const datos = Object.fromEntries(new FormData(formulario));
    const resultado = await enviar("/api/camaras", datos);
    registro.close();
    aviso(resultado.mensaje);
    await refrescar();
  } catch (error) {
    porId("error-registro").textContent = error.message;
    porId("error-registro").hidden = false;
  } finally {
    porId("guardar").disabled = false;
  }
});

async function buscar() {
  const boton = porId("buscar");
  boton.disabled = true;
  porId("busqueda").hidden = false;
  porId("detalle-busqueda").textContent = "Buscando equipos desde la Zero…";
  porId("resultados").replaceChildren();
  try {
    const resultado = await enviar("/api/buscar");
    porId("detalle-busqueda").textContent = resultado.camaras.length
      ? "Seleccioná una IP para registrar o editar la cámara."
      : "No respondieron equipos ONVIF en " + resultado.interfaz + ". Podés registrar la IP manualmente.";
    for (const cam of resultado.camaras) {
      const item = document.createElement("li");
      const botonCamara = document.createElement("button");
      botonCamara.type = "button";
      botonCamara.textContent = cam.ip + " · " + cam.nombre;
      botonCamara.addEventListener("click", () => abrirRegistro(null, cam));
      item.append(botonCamara);
      porId("resultados").append(item);
    }
  } catch (error) {
    porId("detalle-busqueda").textContent = error.message;
  } finally {
    boton.disabled = false;
  }
}

porId("buscar").addEventListener("click", buscar);
porId("cerrar-busqueda").addEventListener("click", () => { porId("busqueda").hidden = true; });
porId("registrar").addEventListener("click", () => abrirRegistro());
porId("probar-todas").addEventListener("click", () => capturar());
porId("cerrar-registro").addEventListener("click", () => registro.close());
registro.addEventListener("close", () => formulario.reset());
campo("tipo").addEventListener("change", mostrarTipo);

refrescar();
setInterval(refrescar, 3500);
