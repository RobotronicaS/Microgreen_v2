"""SAM2 segmenta objetos; el filtro RGB selecciona candidatos vegetales.

No se cuentan plantas, ni se estiman altura, enfermedades o NDVI.
"""
from contextlib import nullcontext
from io import BytesIO
import os
from pathlib import Path
from threading import Lock
import time

import numpy as np
from PIL import Image, ImageDraw, ImageOps

ALGORITMO = "sam2.1-tiny-verde-v1"
_modelo = None
_candado = Lock()


def region_valida(roi):
    if not isinstance(roi, (list, tuple)) or len(roi) != 4:
        raise ValueError("Indicá la región como x, y, ancho y alto normalizados.")
    try:
        x, y, w, h = [float(v) for v in roi]
    except (TypeError, ValueError):
        raise ValueError("Región inválida.") from None
    if not all(np.isfinite(v) for v in (x, y, w, h)) or min(x, y) < 0 or min(w, h) <= 0 or x+w > 1.000001 or y+h > 1.000001:
        raise ValueError("La región debe quedar dentro de la imagen.")
    return x, y, w, h


def cargar_modelo():
    global _modelo
    if _modelo is None:
        import torch
        from sam2.build_sam import build_sam2
        from sam2.automatic_mask_generator import SAM2AutomaticMaskGenerator
        if not torch.cuda.is_available():
            raise RuntimeError("La GPU CUDA no está disponible.")
        ruta = Path(os.getenv("MGV_SAM2_PESOS", "/workspace/microgreen/modelos/sam2.1_hiera_tiny.pt"))
        if not ruta.is_file():
            raise RuntimeError("Faltan los pesos de SAM2.")
        modelo = build_sam2("configs/sam2.1/sam2.1_hiera_t.yaml", str(ruta), device="cuda", apply_postprocessing=False)
        _modelo = SAM2AutomaticMaskGenerator(modelo, points_per_side=16, points_per_batch=16,
                                            pred_iou_thresh=.86, stability_score_thresh=.92,
                                            min_mask_region_area=0)
    return _modelo


def segmentar(imagen, roi, generador=None):
    inicio = time.monotonic()
    x, y, w, h = region_valida(roi)
    imagen = ImageOps.exif_transpose(imagen).convert("RGB")
    imagen.thumbnail((1280, 1280))
    ancho, alto = imagen.size
    a, b = min(ancho-1, round(x*ancho)), min(alto-1, round(y*alto))
    c, d = min(ancho, max(a+1, round((x+w)*ancho))), min(alto, max(b+1, round((y+h)*alto)))
    recorte = imagen.crop((a, b, c, d))
    recorte.thumbnail((1024, 1024))
    rgb = np.array(recorte, copy=True)
    r, g, z = np.moveaxis(rgb.astype(np.float32), -1, 0)
    if float(np.median(np.mean(rgb, axis=2))) < 20:
        raise ValueError("Foto demasiado oscura para medir cobertura.")
    if float(np.mean(np.max(rgb, axis=2)-np.min(rgb, axis=2))) < 3:
        raise ValueError("El análisis de verde requiere una foto a color; no se aplica a IR nocturno.")
    verde = (2*g-r-z > 24) & (g > 1.08*r) & (g > 1.06*z) & (g >= 35)
    mascara = np.zeros(verde.shape, dtype=bool)
    aceptadas = 0
    with _candado:
        if generador is None:
            import torch
            generador = cargar_modelo()
            contexto = torch.autocast("cuda", dtype=torch.bfloat16)
        else:
            contexto = nullcontext()
        with contexto:
            candidatas = generador.generate(rgb)
        for candidata in candidatas:
            objeto = np.asarray(candidata["segmentation"], dtype=bool)
            if objeto.shape != verde.shape:
                raise RuntimeError("El modelo devolvió una máscara inválida.")
            area = int(objeto.sum())
            if area >= 16 and float(verde[objeto].mean()) >= .50:
                mascara |= objeto
                aceptadas += 1
    cobertura = float(mascara.mean()*100)
    insuficiente = bool(verde.mean() > .01 and mascara.sum() < verde.sum()*.10)
    vista = imagen.copy()
    marca = Image.fromarray(mascara.astype(np.uint8)*115).resize((c-a, d-b), Image.Resampling.NEAREST)
    vista.paste((70, 230, 105), (a, b, c, d), marca)
    ImageDraw.Draw(vista).rectangle((a, b, c-1, d-1), outline=(250, 210, 70), width=3)
    vista.thumbnail((860, 620))
    salida = BytesIO()
    vista.save(salida, "PNG")
    return dict(algoritmo=ALGORITMO, cobertura=cobertura, regiones=aceptadas,
                validacion='insuficiente' if insuficiente else 'estimacion',
                segundos=round(time.monotonic()-inicio, 3), roi=[x,y,w,h],
                advertencia=("Segmentación insuficiente respecto al verde visible: revisá la región y no uses este porcentaje como medición."
                             if insuficiente else "Estimación experimental: SAM2 con selección por color verde. Las regiones no son un conteo de plantas.")), salida.getvalue()
