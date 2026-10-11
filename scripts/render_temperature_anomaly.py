"""Renderiza la anomalia diaria de temperatura de Colombia (ERA5) con el
motor nativo de forge3d (CategoricalRasterStyle), al estilo del post de
Milos Popovic sobre Europa en agosto (https://lnkd.in/p/ej6MgpQD) — ese post
usa forge3d por debajo (el link del post apunta al propio repo de forge3d).

MapScene con iluminacion 3D distorsiona los colores clasificados (son
"blended into terrain albedo" antes del tonemap), asi que usamos el color
de paleta directo de ``style.apply(...).rgba`` — la referencia exacta de
paleta segun la documentacion de forge3d — y componemos titulo/leyenda con
Pillow, ya que la composicion de MapPlate (titulos/leyendas/escala) es una
funcion Pro de forge3d que no esta disponible aqui.

Entrada: assets/era5/colombia_t2m_anomaly_<YYYYMM>.tif (una banda por dia,
generado por scripts/fetch_era5_colombia.py).

Salida:
    docs/assets/colombia_temp_anomaly_<YYYYMM>_grid.png   (mosaico del mes)
    docs/assets/colombia_temp_anomaly_<YYYYMM>.gif        (animacion diaria)
    docs/assets/colombia_temp_anomaly_<YYYYMM>.mp4        (video con transicion)
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import forge3d as f3d
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import rasterio
from PIL import Image, ImageDraw, ImageFont
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

BG = (11, 11, 15)
FG = (235, 235, 235)
MUTED = (160, 160, 165)
FONT_DIR = Path(r"C:\Windows\Fonts")
BIN_EDGES = np.linspace(-4.0, 4.0, 15)  # fijo: mismos bins/colores todos los dias


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(str(FONT_DIR / path), size)
    except OSError:
        return ImageFont.load_default()


def _target_month() -> tuple[int, int]:
    if len(sys.argv) >= 3:
        return int(sys.argv[1]), int(sys.argv[2])
    today = date.today()
    year, month = today.year, today.month - 1
    if month == 0:
        year, month = year - 1, 12
    return year, month


def _load_masked_stack(tif_path: Path, boundary: gpd.GeoDataFrame) -> np.ndarray:
    with rasterio.open(tif_path) as src:
        geom = [boundary.geometry.iloc[0].__geo_interface__]
        n_bands = src.count
        frames = []
        for b in range(1, n_bands + 1):
            data, _ = rio_mask(src, geom, indexes=[b], crop=True, nodata=np.nan, filled=True)
            frames.append(data[0])
        return np.stack(frames, axis=0)


def _build_style() -> f3d.CategoricalRasterStyle:
    cmap = plt.get_cmap("RdBu_r")
    n_classes = len(BIN_EDGES) + 1
    palette = {
        i + 1: tuple(int(c * 255) for c in cmap(i / (n_classes - 1))[:3]) + (255,)
        for i in range(n_classes)
    }
    labels = {i + 1: f"class_{i + 1}" for i in range(n_classes)}
    return f3d.CategoricalRasterStyle(palette, labels, nodata=0)


def _classify(day: np.ndarray) -> np.ndarray:
    classed = np.digitize(day, BIN_EDGES) + 1
    return np.where(np.isnan(day), 0, classed).astype(np.int32)


def _native_rgba(day: np.ndarray, style: f3d.CategoricalRasterStyle) -> np.ndarray:
    """Color de paleta exacto de forge3d para un dia (sin iluminacion 3D)."""
    return style.apply(_classify(day)).rgba


def _blend_rgba(rgba_a: np.ndarray, rgba_b: np.ndarray, t: float) -> np.ndarray:
    a = rgba_a.astype(np.float32)
    b = rgba_b.astype(np.float32)
    return np.clip(a * (1 - t) + b * t, 0, 255).astype(np.uint8)


def _compose_card(
    rgba: np.ndarray, title: str, subtitle: str, upscale: int = 12,
) -> Image.Image:
    """Escala el raster de forge3d y compone titulo/leyenda estilo 'card'."""
    base = Image.fromarray(rgba, mode="RGBA").resize(
        (rgba.shape[1] * upscale, rgba.shape[0] * upscale), Image.LANCZOS
    )

    pad_top, pad_bottom, pad_x = 110, 130, 40
    card = Image.new(
        "RGB", (base.width + 2 * pad_x, base.height + pad_top + pad_bottom), BG
    )
    card.paste(base, (pad_x, pad_top), base)

    draw = ImageDraw.Draw(card)
    f_title = _font("arial.ttf", 34)
    f_sub = _font("arial.ttf", 18)
    f_legend = _font("arial.ttf", 15)

    draw.text((pad_x, 28), title, font=f_title, fill=FG)
    draw.text((pad_x, 72), subtitle, font=f_sub, fill=MUTED)

    # Leyenda: barra de color continua RdBu_r con marcas en los bin edges.
    legend_y = base.height + pad_top + 30
    legend_x0, legend_x1 = pad_x, card.width - pad_x
    legend_w = legend_x1 - legend_x0
    cmap = plt.get_cmap("RdBu_r")
    for i in range(legend_w):
        t = i / (legend_w - 1)
        r, g, b = (int(c * 255) for c in cmap(t)[:3])
        draw.line([(legend_x0 + i, legend_y), (legend_x0 + i, legend_y + 14)], fill=(r, g, b))
    for edge in (-4, -2, 0, 2, 4):
        t = (edge - BIN_EDGES[0]) / (BIN_EDGES[-1] - BIN_EDGES[0])
        x = legend_x0 + int(t * legend_w)
        draw.line([(x, legend_y), (x, legend_y + 18)], fill=MUTED)
        label = f"{edge:+d}"
        draw.text((x - 8, legend_y + 20), label, font=f_legend, fill=MUTED)
    draw.text(
        (legend_x0, legend_y + 42), "Anomalia de temperatura (°C)",
        font=f_legend, fill=MUTED,
    )

    return card


def render_grid(stack: np.ndarray, style: f3d.CategoricalRasterStyle, year: int, month: int) -> Path:
    n_days = stack.shape[0]
    n_cols, cell_upscale = 6, 4
    n_rows = int(np.ceil(n_days / n_cols))

    rgba_frames = [_native_rgba(stack[i], style) for i in range(n_days)]
    h, w = rgba_frames[0].shape[:2]
    cell_w, cell_h = w * cell_upscale, h * cell_upscale
    label_h = 26
    margin = 6

    grid_img = Image.new(
        "RGB",
        (n_cols * (cell_w + margin) + margin, n_rows * (cell_h + label_h + margin) + 90),
        BG,
    )
    draw = ImageDraw.Draw(grid_img)
    f_title = _font("arial.ttf", 26)
    f_sub = _font("arial.ttf", 14)
    f_day = _font("arial.ttf", 13)

    month_name = MESES_ES[month]
    draw.text((margin, 14), f"Anomalia de temperatura en Colombia — {month_name} {year}", font=f_title, fill=FG)
    draw.text(
        (margin, 48),
        "Diferencia diaria vs. promedio 1991-2020 a 2 m — ERA5 (Copernicus C3S) · clasificado con forge3d",
        font=f_sub, fill=MUTED,
    )

    for i, rgba in enumerate(rgba_frames):
        row, col = divmod(i, n_cols)
        cell = Image.fromarray(rgba, mode="RGBA").resize((cell_w, cell_h), Image.LANCZOS)
        x = margin + col * (cell_w + margin)
        y = 90 + row * (cell_h + label_h + margin)
        grid_img.paste(cell, (x, y), cell)
        draw.text((x, y + cell_h + 2), f"{i + 1:02d}", font=f_day, fill=MUTED)

    out_path = OUT_DIR / f"colombia_temp_anomaly_{year:04d}{month:02d}_grid.png"
    grid_img.save(out_path)
    print(f"[ok] mosaico guardado en {out_path}")
    return out_path


def render_gif(stack: np.ndarray, style: f3d.CategoricalRasterStyle, year: int, month: int) -> Path:
    import imageio.v2 as imageio

    n_days = stack.shape[0]
    month_name = MESES_ES[month]
    subtitle = "Diferencia diaria vs. promedio 1991-2020 a 2 m — ERA5 (Copernicus C3S)"

    frames = []
    for i in range(n_days):
        rgba = _native_rgba(stack[i], style)
        card = _compose_card(rgba, f"{i + 1:02d} de {month_name} {year}", subtitle)
        frames.append(np.asarray(card))

    out_path = OUT_DIR / f"colombia_temp_anomaly_{year:04d}{month:02d}.gif"
    imageio.mimsave(out_path, frames, duration=0.35, loop=0)
    print(f"[ok] gif guardado en {out_path}")
    return out_path


def render_video(
    stack: np.ndarray, style: f3d.CategoricalRasterStyle, year: int, month: int,
    fps: int = 24, steps_per_day: int = 10,
) -> Path:
    import imageio.v2 as imageio

    n_days = stack.shape[0]
    month_name = MESES_ES[month]
    subtitle = "Diferencia diaria vs. promedio 1991-2020 a 2 m — ERA5 (Copernicus C3S)"
    rgba_days = [_native_rgba(stack[i], style) for i in range(n_days)]

    out_path = OUT_DIR / f"colombia_temp_anomaly_{year:04d}{month:02d}.mp4"
    writer = imageio.get_writer(out_path, fps=fps, codec="libx264", quality=8)
    try:
        for day in range(n_days):
            start, end = rgba_days[day], rgba_days[min(day + 1, n_days - 1)]
            for s in range(steps_per_day):
                t = s / steps_per_day
                blended = _blend_rgba(start, end, t)
                card = _compose_card(blended, f"{day + 1:02d} de {month_name} {year}", subtitle)
                writer.append_data(np.asarray(card))
        last_card = _compose_card(rgba_days[-1], f"{n_days:02d} de {month_name} {year}", subtitle)
        for _ in range(fps):
            writer.append_data(np.asarray(last_card))
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
    stack = _load_masked_stack(tif_path, boundary)
    style = _build_style()

    render_grid(stack, style, year, month)
    render_gif(stack, style, year, month)
    render_video(stack, style, year, month)


if __name__ == "__main__":
    main()
