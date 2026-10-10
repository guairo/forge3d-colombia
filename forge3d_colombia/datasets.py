"""Registro de datasets de terreno colombiano para usar con forge3d.

Cada entrada resuelve a un GeoTIFF (DEM) local empaquetado junto al paquete.
Los datos elevacionales provienen de Copernicus GLO-30 (ESA / Copernicus
Programme, dominio publico via AWS Open Data), sin necesidad de API key.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

_ASSETS_DIR = Path(__file__).resolve().parents[1] / "assets" / "tif"


@dataclass(frozen=True)
class _Dataset:
    name: str
    kind: str
    filename: str
    description: str
    source: str
    bounds_wgs84: str


_DATASETS: Dict[str, _Dataset] = {
    "la-guajira": _Dataset(
        name="la-guajira",
        kind="dem",
        filename="la_guajira_macuira_utm19n.tif",
        description=(
            "Serrania de la Macuira, La Guajira: bosque nuboso aislado que se "
            "alza hasta ~855 m sobre el desierto costero, cerca de Punta "
            "Gallinas, el punto mas septentrional de Sudamerica."
        ),
        source=(
            "Copernicus GLO-30 DEM (ESA/Copernicus Programme), resolucion ~30 m; "
            "recortado y reproyectado a UTM 19N (EPSG:32619) para uso en forge3d"
        ),
        bounds_wgs84="lat 11.90 a 12.20 N, lon -71.80 a -71.30 W",
    ),
}


def _require_data_file(path: Path) -> Path:
    if not path.exists():
        raise FileNotFoundError(
            f"No se encontro el dataset: {path}. Verifica que assets/tif este presente "
            "en el repositorio forge3d-colombia."
        )
    return path


def available() -> List[str]:
    """Nombres de todos los datasets disponibles."""
    return sorted(_DATASETS)


def list_datasets() -> List[Dict[str, str]]:
    """Registro completo de datasets con su metadata."""
    return [dataset_info()[name] | {"name": name} for name in available()]


def dataset_info() -> Dict[str, Dict[str, str]]:
    """Metadata corta por dataset."""
    return {
        name: {
            "kind": meta.kind,
            "filename": meta.filename,
            "description": meta.description,
            "source": meta.source,
            "bounds_wgs84": meta.bounds_wgs84,
        }
        for name, meta in _DATASETS.items()
    }


def fetch_dem(name: str) -> Path:
    """Devuelve la ruta local a un DEM de Colombia por nombre.

    Ejemplo:
        >>> fetch_dem("la-guajira")
    """
    try:
        meta = _DATASETS[name]
    except KeyError as exc:
        raise KeyError(
            f"Dataset desconocido '{name}'. Disponibles: {', '.join(available())}"
        ) from exc
    if meta.kind != "dem":
        raise ValueError(f"El dataset '{name}' no es un DEM (kind='{meta.kind}').")
    return _require_data_file(_ASSETS_DIR / meta.filename)
