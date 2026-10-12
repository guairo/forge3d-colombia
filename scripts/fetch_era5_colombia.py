"""Descarga ERA5-Land (Copernicus CDS) y calcula anomalia de temperatura
diaria para Colombia, analoga al post de Milos Popovic sobre Europa en
agosto (https://lnkd.in/p/ej6MgpQD) — mismo dataset y resolucion que el
original ("ERA5-Land daily means", ~9 km, segun su atribucion).

Requiere:
    pip install cdsapi xarray netCDF4 rasterio
    Cuenta gratuita en https://cds.climate.copernicus.eu/ con API key
    configurada en ~/.cdsapirc, por ejemplo:

        url: https://cds.climate.copernicus.eu/api
        key: <tu-api-key>

    y haber aceptado los terminos de licencia de los datasets:
        - "ERA5-Land hourly data from 1950 to present" (usada via el
          derivado "ERA5-Land daily statistics")
        - "ERA5-Land monthly averaged data from 1950 to present"

Uso:
    python scripts/fetch_era5_colombia.py              # mes anterior completo
    python scripts/fetch_era5_colombia.py 2026 08       # mes/anio especifico
"""

from __future__ import annotations

import sys
from calendar import monthrange
from datetime import date
from pathlib import Path

import zipfile

import cdsapi
import numpy as np
import rasterio
import xarray as xr
from rasterio.transform import from_bounds

# Bounding box de Colombia (incluye territorio insular cercano al continente),
# en formato CDS: [norte, oeste, sur, este]
COLOMBIA_BBOX = [13.5, -79.5, -4.5, -66.5]

# Periodo de referencia (climatologia) para calcular la anomalia.
BASELINE_YEARS = list(range(1991, 2021))  # 1991-2020, estandar WMO

DATA_DIR = Path(__file__).resolve().parents[1] / "assets" / "era5"
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _target_month() -> tuple[int, int]:
    if len(sys.argv) >= 3:
        return int(sys.argv[1]), int(sys.argv[2])
    today = date.today()
    year, month = today.year, today.month - 1
    if month == 0:
        year, month = year - 1, 12
    return year, month


def fetch_daily_t2m(year: int, month: int) -> Path:
    """Descarga la media diaria de t2m de ERA5-Land (~9 km) para Colombia.

    Usa el producto derivado "daily statistics", que calcula el promedio
    diario del lado del servidor (evita bajar los 24 pasos horarios por dia
    y promediarlos localmente).
    """
    out_path = DATA_DIR / f"era5land_t2m_daily_{year:04d}{month:02d}.nc"
    if out_path.exists():
        print(f"[skip] ya existe {out_path}")
        return out_path

    n_days = monthrange(year, month)[1]
    client = cdsapi.Client()
    client.retrieve(
        "derived-era5-land-daily-statistics",
        {
            "variable": "2m_temperature",
            "year": f"{year:04d}",
            "month": f"{month:02d}",
            "day": [f"{d:02d}" for d in range(1, n_days + 1)],
            "daily_statistic": "daily_mean",
            "time_zone": "utc+00:00",
            "frequency": "1_hourly",
            "area": COLOMBIA_BBOX,
        },
        str(out_path),
    )
    print(f"[ok] descargado {out_path}")
    return out_path


def fetch_monthly_climatology(month: int) -> Path:
    """Descarga t2m mensual promedio de ERA5-Land para el baseline 1991-2020.

    El CDS a veces entrega este producto como un .zip (con un .nc adentro)
    en lugar de un netCDF directo; se detecta y extrae automaticamente.
    """
    raw_path = DATA_DIR / f"era5land_t2m_monthly_clim_month{month:02d}_raw.nc"
    extract_dir = DATA_DIR / f"era5land_t2m_monthly_clim_month{month:02d}_extracted"

    if not raw_path.exists():
        client = cdsapi.Client()
        client.retrieve(
            "reanalysis-era5-land-monthly-means",
            {
                "product_type": "monthly_averaged_reanalysis",
                "variable": "2m_temperature",
                "year": [f"{y:04d}" for y in BASELINE_YEARS],
                "month": f"{month:02d}",
                "time": "00:00",
                "area": COLOMBIA_BBOX,
                "format": "netcdf",
            },
            str(raw_path),
        )
        print(f"[ok] descargado {raw_path}")
    else:
        print(f"[skip] ya existe {raw_path}")

    if zipfile.is_zipfile(raw_path):
        if not extract_dir.exists() or not list(extract_dir.glob("*.nc")):
            extract_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(raw_path) as zf:
                zf.extractall(extract_dir)
            print(f"[ok] extraido en {extract_dir}")
        nc_files = sorted(extract_dir.glob("*.nc"))
        if len(nc_files) != 1:
            raise RuntimeError(
                f"Se esperaba un unico .nc en {extract_dir}, se encontraron {len(nc_files)}"
            )
        return nc_files[0]

    return raw_path


def compute_daily_anomalies(daily_path: Path, clim_path: Path, year: int, month: int) -> Path:
    """Calcula anomalia diaria (°C) = media_diaria - climatologia_mensual.

    Guarda un GeoTIFF multibanda (una banda por dia) en assets/era5/.
    """
    ds_daily = xr.open_dataset(daily_path)
    var_name = "t2m" if "t2m" in ds_daily else list(ds_daily.data_vars)[0]
    daily_mean = ds_daily[var_name] - 273.15  # K -> C

    time_dim = "valid_time" if "valid_time" in daily_mean.dims else (
        "time" if "time" in daily_mean.dims else "date"
    )

    ds_clim = xr.open_dataset(clim_path)
    clim_var = "t2m" if "t2m" in ds_clim else list(ds_clim.data_vars)[0]
    clim_time_dim = "valid_time" if "valid_time" in ds_clim[clim_var].dims else "time"
    clim_mean = (ds_clim[clim_var] - 273.15).mean(dim=clim_time_dim)

    anomaly = daily_mean - clim_mean

    lat = anomaly["latitude"].values
    lon = anomaly["longitude"].values
    n_days = anomaly.sizes[time_dim]

    # lat/lon de ERA5 son centros de celda; from_bounds espera los bordes
    # externos del raster, asi que se extiende medio paso en cada lado para
    # no dejar el GeoTIFF corrido ~medio pixel (~4.5 km) respecto al grid real.
    dx = (float(lon.max()) - float(lon.min())) / (len(lon) - 1) if len(lon) > 1 else 0.0
    dy = (float(lat.max()) - float(lat.min())) / (len(lat) - 1) if len(lat) > 1 else 0.0
    transform = from_bounds(
        float(lon.min()) - dx / 2, float(lat.min()) - dy / 2,
        float(lon.max()) + dx / 2, float(lat.max()) + dy / 2,
        len(lon), len(lat),
    )

    out_path = DATA_DIR / f"colombia_t2m_anomaly_{year:04d}{month:02d}.tif"
    data = anomaly.values.astype("float32")
    if lat[0] < lat[-1]:
        data = data[:, ::-1, :]

    with rasterio.open(
        out_path,
        "w",
        driver="GTiff",
        height=data.shape[1],
        width=data.shape[2],
        count=n_days,
        dtype="float32",
        crs="EPSG:4326",
        transform=transform,
        nodata=np.nan,
    ) as dst:
        for i in range(n_days):
            dst.write(data[i], i + 1)
            dst.set_band_description(i + 1, f"day_{i + 1:02d}")

    print(f"[ok] anomalia diaria guardada en {out_path} ({n_days} bandas, resolucion ERA5-Land)")
    return out_path


def main() -> None:
    year, month = _target_month()
    print(f"Mes objetivo: {year:04d}-{month:02d}")
    daily = fetch_daily_t2m(year, month)
    clim = fetch_monthly_climatology(month)
    compute_daily_anomalies(daily, clim, year, month)


if __name__ == "__main__":
    main()
