import os
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, SecretStr, StringConstraints, ValidationError, model_validator


RAIZ = Path(__file__).resolve().parent.parent
Identificador = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")]


class BaseDatos(BaseModel):
    host: str = "127.0.0.1"
    puerto: int = Field(default=5432, ge=1, le=65535)
    nombre: str
    usuario: str
    clave: SecretStr


class Camara(BaseModel):
    id: Identificador
    nombre: str
    hilera: str = ""
    atraso_minutos: int = Field(default=65, gt=0)


class Nodo(BaseModel):
    id: Identificador
    entrada: Path
    camaras: list[Camara] = Field(min_length=1)


class Vivero(BaseModel):
    id: Identificador
    nombre: str
    nodos: list[Nodo] = Field(min_length=1)

    @property
    def camaras(self):
        return [camara for nodo in self.nodos for camara in nodo.camaras]


class Configuracion(BaseModel):
    base_datos: BaseDatos
    archivo: Path
    zona_horaria: str = "America/Montevideo"
    viveros: list[Vivero] = Field(default_factory=list)
    max_jpg_mib: int = Field(default=32, ge=1, le=64)
    max_megapixeles: int = Field(default=40, ge=1, le=80)
    min_libre_gib: float = Field(default=2, ge=0.1)
    cookies_seguras: bool = False

    @model_validator(mode="after")
    def validar_catalogo(self):
        try:
            ZoneInfo(self.zona_horaria)
        except ZoneInfoNotFoundError as error:
            raise ValueError("La zona horaria debe existir en el sistema.") from error

        self.archivo = self.archivo.expanduser().resolve()
        viveros_vistos = set()
        entradas = []
        for vivero in self.viveros:
            if vivero.id in viveros_vistos:
                raise ValueError(f"Vivero duplicado: {vivero.id}.")
            viveros_vistos.add(vivero.id)
            nodos = set()
            camaras = set()
            for nodo in vivero.nodos:
                if nodo.id in nodos:
                    raise ValueError(f"Nodo duplicado en {vivero.id}: {nodo.id}.")
                nodos.add(nodo.id)
                nodo.entrada = nodo.entrada.expanduser().resolve()
                for carpeta in [self.archivo, *entradas]:
                    if carpeta.is_relative_to(nodo.entrada) or nodo.entrada.is_relative_to(carpeta):
                        raise ValueError(f"La entrada de {vivero.id}/{nodo.id} se superpone con otra carpeta.")
                entradas.append(nodo.entrada)
                for camara in nodo.camaras:
                    if camara.id in camaras:
                        raise ValueError(f"Cámara duplicada en {vivero.id}: {camara.id}.")
                    camaras.add(camara.id)
        return self

    @property
    def zona(self):
        return ZoneInfo(self.zona_horaria)


def ruta_configuracion():
    return Path(os.getenv("MGV_CENTRAL_CONFIG", str(RAIZ / "config.json"))).expanduser()


def cargar_configuracion():
    ruta = ruta_configuracion()
    try:
        return Configuracion.model_validate_json(ruta.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(f"Falta {ruta.name}. Ejecutá la instalación primero.") from error
    except ValidationError as error:
        detalles = []
        for item in error.errors(include_input=False, include_url=False):
            campo = ".".join(str(parte) for parte in item["loc"]) or "configuración"
            detalles.append(f"{campo}: {item['msg']}")
        raise ValueError(f"Revisá {ruta.name}: " + " ".join(detalles)) from None
