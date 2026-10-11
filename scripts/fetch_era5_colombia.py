"""Descarga ERA5 (Copernicus CDS) y calcula anomalia de temperatura diaria
para Colombia, analoga al post de Milos Popovic sobre Europa en agosto.

Requiere:
    pip install cdsapi xarray netCDF4 rasterio
    Cuenta gratuita en https://cds.climate.copernicus.eu/ con API key
    configurada en ~/.cdsapirc, por ejemplo:

        url: https://cds.climate.copernicus.eu/api
        key: <tu-api-key>

    y haber aceptado los terminos de licencia de los datasets:
        - "ERA5 hourly data on single levels from 1940 to present"
        - "ERA5 monthly averaged data on single levels from 1940 to present"

Uso:
    python scripts/fetch_era5_colombia.py              # mes anterior completo
    python scripts/fetch_era5_colombia.py 2026 08       # mes/anio especifico
"""

from __future__ import annotations

import sys
from calendar import monthrange
from datetime import date
from pathlib import Path

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
    """Descarga t2m horario de ERA5 para el mes objetivo sobre Colombia."""
    out_path = DATA_DIR / f"era5_t2m_hourly_{year:04d}{month:02d}.nc"
    if out_path.exists():
        print(f"[skip] ya existe {out_path}")
        return out_path

    n_days = monthrange(year, month)[1]
    client = cdsapi.Client()
    client.retrieve(
        "reanalysis-era5-single-levels",
        {
            "product_type": "reanalysis",
            "variable": "2m_temperature",
            "year": f"{year:04d}",
            "month": f"{month:02d}",
            "day": [f"{d:02d}" for d in range(1, n_days + 1)],
            "time": [f"{h:02d}:00" for h in range(24)],
            "area": COLOMBIA_BBOX,
            "format": "netcdf",
        },
        str(out_path),
    )
    print(f"[ok] descargado {out_path}")
    return out_path


def fetch_monthly_climatology(month: int) -> Path:
    """Descarga t2m mensual promedio para el baseline (1991-2020) del mes dado."""
    out_path = DATA_DIR / f"era5_t2m_monthly_clim_month{month:02d}.nc"
    if out_path.exists():
        print(f"[skip] ya existe {out_path}")
        return out_path

    client = cdsapi.Client()
    client.retrieve(
        "reanalysis-era5-single-levels-monthly-means",
        {
            "product_type": "monthly_averaged_reanalysis",
            "variable": "2m_temperature",
            "year": [f"{y:04d}" for y in BASELINE_YEARS],
            "month": f"{month:02d}",
            "time": "00:00",
            "area": COLOMBIA_BBOX,
            "format": "netcdf",
        },
        str(out_path),
    )
    print(f"[ok] descargado {out_path}")
    return out_path


def compute_daily_anomalies(hourly_path: Path, clim_path: Path, year: int, month: int) -> Path:
    """Calcula anomalia diaria (K) = promedio_diario - climatologia_mensual.

    Guarda un GeoTIFF multibanda (una banda por dia) en assets/era5/.
    """
    ds_hourly = xr.open_dataset(hourly_path)
    t2m = ds_hourly["t2m"] - 273.15  # K -> C

    time_dim = "valid_time" if "valid_time" in t2m.dims else "time"
    daily_mean = t2m.resample({time_dim: "1D"}).mean()

    ds_clim = xr.open_dataset(clim_path)
    clim_mean = (ds_clim["t2m"] - 273.15).mean(dim="valid_time" if "valid_time" in ds_clim["t2m"].dims else "time")

    anomaly = daily_mean - clim_mean

    lat = anomaly["latitude"].values
    lon = anomaly["longitude"].values
    n_days = anomaly.sizes[time_dim]

    transform = from_bounds(
        lon.min(), lat.min(), lon.max(), lat.max(), len(lon), len(lat)
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

    print(f"[ok] anomalia diaria guardada en {out_path} ({n_days} bandas)")
    return out_path


def main() -> None:
    year, month = _target_month()
    print(f"Mes objetivo: {year:04d}-{month:02d}")
    hourly = fetch_daily_t2m(year, month)
    clim = fetch_monthly_climatology(month)
    compute_daily_anomalies(hourly, clim, year, month)


if __name__ == "__main__":
    main()
