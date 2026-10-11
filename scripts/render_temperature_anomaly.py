"""Renderiza la anomalia diaria de temperatura de Colombia (ERA5) como un
relieve 3D sombreado con forge3d, al estilo de la galeria del propio motor
(terreno + color tematico) y del post de Milos Popovic sobre Europa en
agosto (https://lnkd.in/p/ej6MgpQD) — ese post usa forge3d por debajo (el
link del post apunta al propio repo de forge3d).

forge3d.MapScene mezcla el color de un RasterOverlay clasificado con el
tonemap de iluminacion 3D antes de mostrarlo (ver docs/guides/
thematic_raster_styles.md: "Lighting and tone mapping can change displayed
RGB"), lo que apaga/ensucia una paleta divergente. Para tener relieve 3D
real Y colores de paleta fieles, el pipeline:

  1. Usa la propia anomalia (suavizada) como altura en un TerrainSource
     neutro (sin color tematico) y renderiza SOLO el sombreado 3D nativo
     de forge3d (luz, sombras, textura de relieve).
  2. Obtiene el color de paleta exacto de forge3d con
     ``CategoricalRasterStyle.apply(...).rgba`` — "the exact palette
     reference" segun la documentacion, sin pasar por el motor de luz.
  3. Combina ambos con un blend multiplicativo (color x sombreado), la
     tecnica estandar de cartografia para unir hillshade + color tematico.

Entrada: assets/era5/colombia_t2m_anomaly_<YYYYMM>.tif (una banda por dia,
generado por scripts/fetch_era5_colombia.py).

Salida:
    docs/assets/colombia_temp_anomaly_<YYYYMM>_grid.png   (mosaico del mes)
    docs/assets/colombia_temp_anomaly_<YYYYMM>.gif        (animacion diaria)
    docs/assets/colombia_temp_anomaly_<YYYYMM>.mp4        (video con transicion)

Requiere una GPU compatible con WebGPU (igual que el resto de forge3d).
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
from scipy.ndimage import gaussian_filter, zoom

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
UPSCALE = 4  # suavizado de la rejilla nativa ERA5 (~0.25 grados)
RENDER_W, RENDER_H = 900, 1080
NEUTRAL_GRAY = 235


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


def _smooth_upsample(day: np.ndarray) -> np.ndarray:
    """Suaviza la rejilla nativa ERA5 (gruesa) para un relieve 3D legible."""
    valid = ~np.isnan(day)
    filled = np.where(valid, day, 0.0)
    up_filled = gaussian_filter(zoom(filled, UPSCALE, order=1), sigma=2.0)
    up_valid = zoom(valid.astype(np.float32), UPSCALE, order=1) > 0.5
    return np.where(up_valid, up_filled, np.nan)


def _build_style() -> f3d.CategoricalRasterStyle:
    cmap = plt.get_cmap("RdBu_r")
    n_classes = len(BIN_EDGES) + 1
    palette = {
        i + 1: tuple(int(c * 255) for c in cmap(i / (n_classes - 1))[:3]) + (255,)
        for i in range(n_classes)
    }
    labels = {i + 1: f"class_{i + 1}" for i in range(n_classes)}
    return f3d.CategoricalRasterStyle(palette, labels, nodata=0)


def _palette_rgba(day_up: np.ndarray, style: f3d.CategoricalRasterStyle) -> np.ndarray:
    classed = np.digitize(day_up, BIN_EDGES) + 1
    classed = np.where(np.isnan(day_up), 0, classed).astype(np.int32)
    small = style.apply(classed).rgba
    return np.asarray(
        Image.fromarray(small, mode="RGBA").resize((RENDER_W, RENDER_H), Image.NEAREST)
    )


def _render_shade(day_up: np.ndarray, tmp_path: Path) -> np.ndarray:
    """Relieve 3D neutro (solo luz/sombra) con el motor nativo de forge3d."""
    valid = ~np.isnan(day_up)
    height_src = np.where(valid, day_up + 5.0, 0.0).astype(np.float32)
    neutral_style = f3d.RasterHeightSurfaceStyle(
        900.0, "C",
        low_color=(NEUTRAL_GRAY, NEUTRAL_GRAY, NEUTRAL_GRAY, 255),
        high_color=(NEUTRAL_GRAY, NEUTRAL_GRAY, NEUTRAL_GRAY, 255),
    )
    terrain = f3d.TerrainSource(
        data=height_src, style=neutral_style, crs="EPSG:4326",
        metadata={"source_id": "anomaly_relief"}, elevation_sampling_available=True,
    )
    camera = f3d.OrbitCamera(distance=2.5, azimuth_deg=-40, elevation_deg=42, fov_deg=38)
    output = f3d.OutputSpec(width=RENDER_W, height=RENDER_H, format="png", path=str(tmp_path))
    lighting = f3d.LightingPreset(name="daylight", sun_direction=(0.4, 0.9, 0.3), intensity=1.6)
    scene = f3d.MapScene(terrain=terrain, layers=(), camera=camera, lighting=lighting, output=output)
    scene.render(str(tmp_path))
    return np.asarray(Image.open(tmp_path).convert("L"), dtype=np.float32)


def _compose_day_rgba(
    day: np.ndarray, style: f3d.CategoricalRasterStyle, tmp_path: Path,
) -> np.ndarray:
    """Relieve sombreado (forge3d) x color de paleta (forge3d) = render final."""
    day_up = _smooth_upsample(day)
    color = _palette_rgba(day_up, style).astype(np.float32)
    shade = _render_shade(day_up, tmp_path)
    shade_norm = np.clip(shade / NEUTRAL_GRAY, 0.45, 1.4)
    color[..., :3] = np.clip(color[..., :3] * shade_norm[..., None], 0, 255)
    return color.astype(np.uint8)


def _compose_card(rgba: np.ndarray, title: str, subtitle: str) -> Image.Image:
    base = Image.fromarray(rgba, mode="RGBA")
    pad_top, pad_bottom, pad_x = 110, 130, 40
    card = Image.new("RGB", (base.width + 2 * pad_x, base.height + pad_top + pad_bottom), BG)
    card.paste(base, (pad_x, pad_top), base)

    draw = ImageDraw.Draw(card)
    f_title = _font("arial.ttf", 34)
    f_sub = _font("arial.ttf", 18)
    f_legend = _font("arial.ttf", 15)

    draw.text((pad_x, 28), title, font=f_title, fill=FG)
    draw.text((pad_x, 72), subtitle, font=f_sub, fill=MUTED)

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
        draw.text((x - 8, legend_y + 20), f"{edge:+d}", font=f_legend, fill=MUTED)
    draw.text(
        (legend_x0, legend_y + 42), "Anomalia de temperatura (°C)",
        font=f_legend, fill=MUTED,
    )
    return card


def render_all_days(stack: np.ndarray, style: f3d.CategoricalRasterStyle) -> list[np.ndarray]:
    tmp_path = OUT_DIR / "_tmp_shade.png"
    frames = []
    for i in range(stack.shape[0]):
        frames.append(_compose_day_rgba(stack[i], style, tmp_path))
        print(f"[render] dia {i + 1:02d}/{stack.shape[0]} listo")
    tmp_path.unlink(missing_ok=True)
    return frames


def render_grid(day_rgbas: list[np.ndarray], year: int, month: int) -> Path:
    n_days = len(day_rgbas)
    n_cols, cell_scale = 6, 0.32
    n_rows = int(np.ceil(n_days / n_cols))
    h, w = day_rgbas[0].shape[:2]
    cell_w, cell_h = int(w * cell_scale), int(h * cell_scale)
    label_h, margin = 22, 6

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
        "Diferencia diaria vs. promedio 1991-2020 a 2 m — ERA5 (Copernicus C3S) · relieve y color via forge3d",
        font=f_sub, fill=MUTED,
    )

    for i, rgba in enumerate(day_rgbas):
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


def render_gif(day_rgbas: list[np.ndarray], year: int, month: int) -> Path:
    import imageio.v2 as imageio

    month_name = MESES_ES[month]
    subtitle = "Diferencia diaria vs. promedio 1991-2020 a 2 m — ERA5 (Copernicus C3S)"
    frames = [
        np.asarray(_compose_card(rgba, f"{i + 1:02d} de {month_name} {year}", subtitle))
        for i, rgba in enumerate(day_rgbas)
    ]
    out_path = OUT_DIR / f"colombia_temp_anomaly_{year:04d}{month:02d}.gif"
    imageio.mimsave(out_path, frames, duration=0.35, loop=0)
    print(f"[ok] gif guardado en {out_path}")
    return out_path


def render_video(
    day_rgbas: list[np.ndarray], year: int, month: int,
    fps: int = 24, steps_per_day: int = 10,
) -> Path:
    import imageio.v2 as imageio

    n_days = len(day_rgbas)
    month_name = MESES_ES[month]
    subtitle = "Diferencia diaria vs. promedio 1991-2020 a 2 m — ERA5 (Copernicus C3S)"
    cards = [np.asarray(_compose_card(rgba, "", subtitle)) for rgba in day_rgbas]

    out_path = OUT_DIR / f"colombia_temp_anomaly_{year:04d}{month:02d}.mp4"
    writer = imageio.get_writer(out_path, fps=fps, codec="libx264", quality=8)
    try:
        for day in range(n_days):
            start, end = cards[day].astype(np.float32), cards[min(day + 1, n_days - 1)].astype(np.float32)
            title = f"{day + 1:02d} de {month_name} {year}"
            for s in range(steps_per_day):
                t = s / steps_per_day
                blended = np.clip(start * (1 - t) + end * t, 0, 255).astype(np.uint8)
                frame = Image.fromarray(blended)
                draw = ImageDraw.Draw(frame)
                draw.text((40, 28), title, font=_font("arial.ttf", 34), fill=FG)
                writer.append_data(np.asarray(frame))
        last = Image.fromarray(cards[-1])
        draw = ImageDraw.Draw(last)
        draw.text((40, 28), f"{n_days:02d} de {month_name} {year}", font=_font("arial.ttf", 34), fill=FG)
        for _ in range(fps):
            writer.append_data(np.asarray(last))
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

    day_rgbas = render_all_days(stack, style)
    render_grid(day_rgbas, year, month)
    render_gif(day_rgbas, year, month)
    render_video(day_rgbas, year, month)


if __name__ == "__main__":
    main()
