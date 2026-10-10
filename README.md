# forge3d-colombia

Fork ligero de [forge3d](https://github.com/milos-agathon/forge3d) (terreno y
cartografia path-traced para Python, motor en Rust + WebGPU) con un registro
de datasets y ejemplos propios centrados en **Colombia**.

Este proyecto no reimplementa el motor de render: usa el paquete `forge3d`
tal cual se publica en PyPI y solo aporta datos de elevacion de Colombia,
scripts de ejemplo y documentacion en espanol.

<p align="center">
  <img src="docs/assets/la_guajira.png" alt="Serrania de la Macuira renderizada con forge3d" width="700">
</p>
<p align="center">
  <sub>Serrania de la Macuira, La Guajira — render generado por
  <code>examples/la_guajira.py</code> sobre un DEM Copernicus GLO-30.</sub>
</p>

## Instalacion

```bash
pip install forge3d
pip install -r requirements.txt
```

## Datasets incluidos

| Dataset      | Descripcion                                                                 | Fuente                              |
|--------------|------------------------------------------------------------------------------|--------------------------------------|
| `la-guajira` | Desierto de La Guajira, Serrania de la Macuira y costa Caribe cerca de Punta Gallinas (punto mas septentrional de Sudamerica) | Copernicus GLO-30 DEM (~30 m), ESA/Copernicus Programme |

```python
from forge3d_colombia.datasets import fetch_dem, list_datasets

print(list_datasets())
dem_path = fetch_dem("la-guajira")
```

## Sesenta segundos a un render

```python
import forge3d as f3d
from forge3d_colombia.datasets import fetch_dem

dem_path = fetch_dem("la-guajira")

with f3d.open_viewer_async(terrain_path=dem_path, width=1440, height=900) as viewer:
    viewer.set_z_scale(0.1)
    viewer.set_orbit_camera(phi_deg=35, theta_deg=55, radius=4200, fov_deg=42)
    viewer.set_sun(azimuth_deg=280, elevation_deg=18)
    viewer.snapshot("la_guajira.png", width=1920, height=1080)
```

Tambien disponible como script: [`examples/la_guajira.py`](examples/la_guajira.py).
Requiere una GPU compatible con WebGPU.

## Agregar mas lugares de Colombia

El registro vive en [`forge3d_colombia/datasets.py`](forge3d_colombia/datasets.py).
Para agregar una nueva zona (p. ej. Nevado del Ruiz, Sierra Nevada de Santa
Marta, Cocuy):

1. Descargar el tile DEM correspondiente (Copernicus GLO-30 es de acceso
   publico sin API key: `https://copernicus-dem-30m.s3.amazonaws.com/`).
2. Guardarlo en `assets/tif/`.
3. Agregar una entrada al diccionario `_DATASETS` con nombre, archivo,
   descripcion y fuente.

## Atribucion y licencia

- Motor de render: [forge3d](https://github.com/milos-agathon/forge3d), (c) 2025-2026
  Milos Popovic, licenciado Apache-2.0 / MIT. Este fork no modifica ese codigo,
  solo depende de el via PyPI.
- Datos de elevacion: Copernicus DEM GLO-30, producido con financiamiento de
  la Comision Europea en el marco del Programa Copernicus, provisto por el
  Agencia Espacial Europea (ESA) de acceso abierto y gratuito.
- Codigo propio de este fork (`forge3d_colombia/`, `examples/`): MIT, ver
  [`LICENSE`](LICENSE).
