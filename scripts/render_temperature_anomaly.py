"""Renderiza la anomalia diaria de temperatura de Colombia (ERA5) como un
mosaico mensual y un GIF animado, al estilo del post de Milos Popovic sobre
Europa en agosto (https://lnkd.in/p/ej6MgpQD).

Entrada: assets/era5/colombia_t2m_anomaly_<YYYYMM>.tif (una banda por dia,
generado por scripts/fetch_era5_colombia.py).

Salida:
    docs/assets/colombia_temp_anomaly_<YYYYMM>_grid.png   (mosaico del mes)
    docs/assets/colombia_temp_anomaly_<YYYYMM>.gif        (animacion diaria)
"""

from __future__ import annotations

import calendar
import sys
from datetime import date
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import rasterio
from matplotlib.colors import TwoSlopeNorm
from rasterio.mask import mask as rio_mask

REPO_ROOT = Path(__file__).resolve().parents[1]
ERA5_DIR = REPO_ROOT / "assets" / "era5"
BOUNDARY_PATH = REPO_ROOT / "assets" / "geojson" / "colombia_boundary.geojson"
OUT_DIR = REPO_ROOT / "docs" / "assets"
OUT_DIR.mkdir(parents=True, exist_ok=True)

MESES_ES = [
    "", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
    "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def _target_month() -> tuple[int, int]:
    if len(sys.argv) >= 3:
        return int(sys.argv[1]), int(sys.argv[2])
    today = date.today()
    year, month = today.year, today.month - 1
    if month == 0:
        year, month = year - 1, 12
    return year, month


def _load_masked_stack(tif_path: Path, boundary: gpd.GeoDataFrame):
    with rasterio.open(tif_path) as src:
        geom = [boundary.geometry.iloc[0].__geo_interface__]
        n_bands = src.count
        frames = []
        transform = None
        for b in range(1, n_bands + 1):
            data, out_transform = rio_mask(
                src, geom, indexes=[b], crop=True, nodata=np.nan, filled=True
            )
            frames.append(data[0])
            transform = out_transform
        return np.stack(frames, axis=0), transform


def render_grid(stack: np.ndarray, year: int, month: int, vmax: float) -> Path:
    n_days = stack.shape[0]
    n_cols = 6
    n_rows = int(np.ceil(n_days / n_cols))

    fig, axes = plt.subplots(
        n_rows, n_cols, figsize=(n_cols * 2.0, n_rows * 2.3),
        facecolor="#0b0b0f",
    )
    norm = TwoSlopeNorm(vmin=-vmax, vcenter=0, vmax=vmax)
    cmap = plt.get_cmap("RdBu_r")

    im = None
    for i, ax in enumerate(axes.flat):
        ax.set_facecolor("#0b0b0f")
        ax.axis("off")
        if i < n_days:
            im = ax.imshow(stack[i], cmap=cmap, norm=norm)
            ax.set_title(f"{i + 1:02d}", color="white", fontsize=9, pad=2)

    month_name = MESES_ES[month]
    fig.suptitle(
        f"Anomalia de temperatura en Colombia — {month_name} {year}",
        color="white", fontsize=16, y=0.99,
    )
    fig.text(
        0.5, 0.965,
        "Diferencia diaria vs. promedio 1991-2020 a 2 m — ERA5 (Copernicus C3S)",
        color="#aaaaaa", fontsize=9.5, ha="center",
    )

    cbar_ax = fig.add_axes([0.25, 0.02, 0.5, 0.015])
    cbar = fig.colorbar(im, cax=cbar_ax, orientation="horizontal")
    cbar.set_label("Anomalia (°C)", color="white", fontsize=9)
    cbar.ax.xaxis.set_tick_params(color="white", labelcolor="white")

    fig.tight_layout(rect=[0, 0.05, 1, 0.95])
    out_path = OUT_DIR / f"colombia_temp_anomaly_{year:04d}{month:02d}_grid.png"
    fig.savefig(out_path, dpi=160, facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"[ok] mosaico guardado en {out_path}")
    return out_path


def _render_frame(
    data: np.ndarray, title: str, norm: TwoSlopeNorm, cmap, figsize=(5, 6), dpi=160
) -> np.ndarray:
    fig, ax = plt.subplots(figsize=figsize, dpi=dpi, facecolor="#0b0b0f")
    ax.set_facecolor("#0b0b0f")
    ax.axis("off")
    im = ax.imshow(data, cmap=cmap, norm=norm)
    ax.set_title(title, color="white", fontsize=13, pad=8)
    cbar = fig.colorbar(im, ax=ax, orientation="horizontal", fraction=0.05, pad=0.04)
    cbar.set_label("Anomalia de temperatura (°C)", color="white", fontsize=9)
    cbar.ax.xaxis.set_tick_params(color="white", labelcolor="white")
    fig.tight_layout()
    fig.canvas.draw()
    frame = np.asarray(fig.canvas.buffer_rgba())[:, :, :3].copy()
    plt.close(fig)
    return frame


def render_gif(stack: np.ndarray, year: int, month: int, vmax: float) -> Path:
    import imageio.v2 as imageio

    n_days = stack.shape[0]
    norm = TwoSlopeNorm(vmin=-vmax, vcenter=0, vmax=vmax)
    cmap = plt.get_cmap("RdBu_r")
    month_name = MESES_ES[month]

    frames = [
        _render_frame(stack[i], f"{i + 1:02d} de {month_name} {year}", norm, cmap)
        for i in range(n_days)
    ]

    out_path = OUT_DIR / f"colombia_temp_anomaly_{year:04d}{month:02d}.gif"
    imageio.mimsave(out_path, frames, duration=0.35, loop=0)
    print(f"[ok] gif guardado en {out_path}")
    return out_path


def render_video(
    stack: np.ndarray, year: int, month: int, vmax: float,
    fps: int = 24, steps_per_day: int = 10,
) -> Path:
    """Video MP4 con transicion suave dia a dia, al estilo del post de Popovic."""
    import imageio.v2 as imageio

    n_days = stack.shape[0]
    norm = TwoSlopeNorm(vmin=-vmax, vcenter=0, vmax=vmax)
    cmap = plt.get_cmap("RdBu_r")
    month_name = MESES_ES[month]

    out_path = OUT_DIR / f"colombia_temp_anomaly_{year:04d}{month:02d}.mp4"
    writer = imageio.get_writer(out_path, fps=fps, codec="libx264", quality=8)
    try:
        for day in range(n_days):
            start = stack[day]
            end = stack[day + 1] if day + 1 < n_days else stack[day]
            for s in range(steps_per_day):
                t = s / steps_per_day
                blended = start * (1 - t) + end * t
                frame = _render_frame(
                    blended, f"{day + 1:02d} de {month_name} {year}", norm, cmap
                )
                writer.append_data(frame)
        # mantener el ultimo dia un poco mas al final
        last_frame = _render_frame(
            stack[-1], f"{n_days:02d} de {month_name} {year}", norm, cmap
        )
        for _ in range(fps):
            writer.append_data(last_frame)
    finally:
        writer.close()

    print(f"[ok] video guardado en {out_path}")
    return out_path


def main() -> None:
    year, month = _target_month()
    tif_path = ERA5_DIR / f"colombia_t2m_anomaly_{year:04d}{month:02d}.tif"
    if not tif_path.exists():
        raise FileNotFoundError(
            f"No existe {tif_path}. Ejecuta primero scripts/fetch_era5_colombia.py"
        )

    boundary = gpd.read_file(BOUNDARY_PATH)
    stack, _ = _load_masked_stack(tif_path, boundary)

    vmax = float(np.nanpercentile(np.abs(stack), 99))
    vmax = max(vmax, 1.0)

    render_grid(stack, year, month, vmax)
    try:
        render_gif(stack, year, month, vmax)
    except ImportError:
        print("[info] imageio no instalado, se omite el GIF (pip install imageio)")
    try:
        render_video(stack, year, month, vmax)
    except ImportError:
        print(
            "[info] imageio-ffmpeg no instalado, se omite el video "
            "(pip install imageio-ffmpeg)"
        )


if __name__ == "__main__":
    main()
