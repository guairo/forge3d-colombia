"""Renderiza la anomalia diaria de temperatura de Colombia (ERA5-Land) como
un relieve 3D al estilo del video original de Milos Popovic sobre Europa
(https://lnkd.in/p/ej6MgpQD, "A warmer shape of summer, day by day") — ese
post usa forge3d por debajo (su enlace a "la herramienta" apunta al propio
repositorio).

Pipeline:
  1. La propia anomalia (sin suavizar, para conservar el detalle de celda
     de ERA5-Land ~9 km) maneja un TerrainSource neutro en forge3d.MapScene,
     que se renderiza SOLO para capturar el sombreado 3D nativo (luz,
     sombras, textura de relieve). Calido sube, frio se hunde.
  2. El color es un colormap continuo construido a partir de la paleta
     exacta muestreada del video original (azul grisaceo -> crema ->
     naranja -> rojo), aplicado directamente sobre la anomalia (-9 a +9 C).
  3. Se combinan con un blend multiplicativo suave (sombreado x color).
  4. Se compone sobre un lienzo blanco con sombra proyectada y un panel
     lateral editorial (titulo, fecha, media diaria, mini histograma,
     leyenda, atribucion), igual que el original.

forge3d.MapScene mezcla el color de un RasterOverlay clasificado con su
propio tonemap de iluminacion (ver docs/guides/thematic_raster_styles.md),
por eso el color final se compone aparte en vez de usar un overlay nativo.
El motor tambien limita height_scale a +-65536 unidades (RuntimeError de
`u_shading.clamp0` si se excede) — el efecto de "pelo/agujas" muy afilado
del video original probablemente usa columnas discretas por celda en vez
de una superficie continua interpolada; esa tecnica no esta expuesta en la
API publica de forge3d usada aqui, asi que este render usa una superficie
continua (menos afilada, mismo concepto de altura+color=anomalia).

Entrada: assets/era5/colombia_t2m_anomaly_<YYYYMM>.tif (una banda por dia,
generado por scripts/fetch_era5_colombia.py con datos ERA5-Land).

Salida:
    docs/assets/colombia_temp_anomaly_<YYYYMM>_grid.png   (mosaico del mes)
    docs/assets/colombia_temp_anomaly_<YYYYMM>.gif        (animacion diaria)
    docs/assets/colombia_temp_anomaly_<YYYYMM>.mp4        (video con transicion)

Requiere una GPU compatible con WebGPU.
"""

from __future__ import annotations

import sys
from calendar import monthrange
from datetime import date
from pathlib import Path

import forge3d as f3d
import geopandas as gpd
import numpy as np
import rasterio
from matplotlib.colors import LinearSegmentedColormap
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from rasterio.mask import mask as rio_mask
from scipy.ndimage import zoom as ndi_zoom

REPO_ROOT = Path(__file__).resolve().parents[1]
ERA5_DIR = REPO_ROOT / "assets" / "era5"
BOUNDARY_PATH = REPO_ROOT / "assets" / "geojson" / "colombia_boundary.geojson"
OUT_DIR = REPO_ROOT / "docs" / "assets"
OUT_DIR.mkdir(parents=True, exist_ok=True)

MESES_ES = [
    "", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
    "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]
DIAS_ES = ["LUNES", "MARTES", "MIERCOLES", "JUEVES", "VIERNES", "SABADO", "DOMINGO"]

FONT_DIR = Path(r"C:\Windows\Fonts")
INK = (35, 35, 38)
MUTED = (120, 120, 126)
FAINT = (170, 170, 176)
WHITE = (255, 255, 255)

ANCHOR_POS = [0.0, 0.081, 0.162, 0.243, 0.324, 0.405, 0.486, 0.567, 0.648, 0.729, 0.810, 0.891, 0.972, 1.0]
ANCHOR_RGB = [
    (38, 81, 122), (52, 103, 154), (70, 121, 172), (89, 140, 191), (134, 169, 201),
    (170, 202, 210), (220, 227, 220), (224, 216, 192), (239, 186, 133), (231, 167, 100),
    (212, 135, 77), (190, 99, 57), (169, 60, 52), (169, 60, 52),
]
CMAP = LinearSegmentedColormap.from_list(
    "popovic", list(zip(ANCHOR_POS, [tuple(c / 255 for c in rgb) for rgb in ANCHOR_RGB]))
)
VRANGE = 9.0  # anomalia en +-9 C mapea a los extremos de la paleta

RENDER_PX = 1100
HEIGHT_SCALE = 3500.0
HEIGHT_OFFSET = 11.0  # mantiene la altura positiva (rango util ~2..20)
NEUTRAL_GRAY = 235
PANEL_W = 430
CANVAS_W, CANVAS_H = PANEL_W + RENDER_PX, RENDER_PX
UPSAMPLE_FACTOR = 3  # suaviza la rejilla nativa de ERA5-Land (169x123) antes de renderizar

# Firma del autor en el panel (pendiente: nombre y LinkedIn reales del usuario)
SIGNATURE_NAME = "TU NOMBRE AQUI"
SIGNATURE_LINKEDIN = "linkedin.com/in/tu-usuario"


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(str(FONT_DIR / name), size)
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
        frames = []
        for b in range(1, src.count + 1):
            data, _ = rio_mask(src, geom, indexes=[b], crop=True, nodata=np.nan, filled=True)
            frames.append(data[0])
        return np.stack(frames, axis=0)


def _upsample_day(day: np.ndarray, mask_valid: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Suaviza la rejilla nativa de ERA5-Land con un upsample bicubico.

    Evita el aspecto "en terrazas" tanto del relieve como del color al
    escalar una rejilla de ~170x120 celdas hasta RENDER_PX.
    """
    filled = np.where(mask_valid, day, 0.0)
    day_up = ndi_zoom(filled, UPSAMPLE_FACTOR, order=3)
    mask_up = ndi_zoom(mask_valid.astype(np.float32), UPSAMPLE_FACTOR, order=1) > 0.5
    return day_up, mask_up


def _render_shade(day_up: np.ndarray, mask_up: np.ndarray, tmp_path: Path) -> np.ndarray:
    """Relieve 3D (altura = anomalia) renderizado con el motor nativo de forge3d."""
    height_src = np.where(mask_up, day_up + HEIGHT_OFFSET, 0.0).astype(np.float32)
    neutral_style = f3d.RasterHeightSurfaceStyle(
        HEIGHT_SCALE, "C",
        low_color=(NEUTRAL_GRAY, NEUTRAL_GRAY, NEUTRAL_GRAY, 255),
        high_color=(NEUTRAL_GRAY, NEUTRAL_GRAY, NEUTRAL_GRAY, 255),
    )
    terrain = f3d.TerrainSource(
        data=height_src, style=neutral_style, crs="EPSG:4326",
        metadata={"source_id": "anomaly_relief"}, elevation_sampling_available=True,
    )
    camera = f3d.OrbitCamera(distance=2.3, azimuth_deg=-25, elevation_deg=46, fov_deg=32)
    output = f3d.OutputSpec(width=RENDER_PX, height=RENDER_PX, format="png", path=str(tmp_path))
    lighting = f3d.LightingPreset(name="daylight", sun_direction=(0.3, 0.9, 0.4), intensity=1.6)
    scene = f3d.MapScene(terrain=terrain, layers=(), camera=camera, lighting=lighting, output=output)
    scene.render(str(tmp_path))
    return np.asarray(Image.open(tmp_path).convert("L"), dtype=np.float32)


def _render_map_rgba(day: np.ndarray, tmp_path: Path) -> Image.Image:
    mask_valid = ~np.isnan(day)
    day_up, mask_up = _upsample_day(day, mask_valid)
    shade = _render_shade(day_up, mask_up, tmp_path)

    norm = np.clip((day_up + VRANGE) / (2 * VRANGE), 0.0, 1.0)
    color = (CMAP(norm)[..., :3] * 255).astype(np.uint8)
    alpha = np.where(mask_up, 255, 0).astype(np.uint8)

    color_img = np.asarray(Image.fromarray(color).resize((RENDER_PX, RENDER_PX), Image.BICUBIC))
    alpha_img = np.asarray(Image.fromarray(alpha).resize((RENDER_PX, RENDER_PX), Image.BILINEAR))

    shade_norm = np.clip(shade / NEUTRAL_GRAY, 0.78, 1.25)
    blended = np.clip(color_img.astype(np.float32) * shade_norm[..., None], 0, 255).astype(np.uint8)

    rgba = np.dstack([blended, alpha_img])
    return Image.fromarray(rgba, mode="RGBA")


def _compose_on_canvas(map_rgba: Image.Image) -> Image.Image:
    """Lienzo blanco + sombra proyectada, sin panel (para el mapa solo)."""
    alpha = map_rgba.split()[-1]
    shadow_alpha = alpha.filter(ImageFilter.GaussianBlur(14)).point(lambda a: int(a * 0.32))
    canvas = Image.new("RGB", map_rgba.size, WHITE)
    shadow_layer = Image.new("RGBA", map_rgba.size, (0, 0, 0, 0))
    shadow_layer.putalpha(shadow_alpha)
    canvas.paste(shadow_layer, (14, 18), shadow_layer)
    canvas.paste(map_rgba, (0, 0), map_rgba)
    return canvas


def _draw_sparkline(draw: ImageDraw.ImageDraw, x0: int, y: int, w: int, h: int, values: list[float], active_idx: int) -> None:
    n = len(values)
    bar_w = w / n
    vmax = max(abs(v) for v in values) or 1.0
    for i, v in enumerate(values):
        norm = np.clip((v + VRANGE) / (2 * VRANGE), 0, 1)
        r, g, b = (int(c * 255) for c in CMAP(norm)[:3])
        bar_h = max(2, int(abs(v) / vmax * h))
        bx0 = x0 + i * bar_w
        by1 = y + h
        by0 = by1 - bar_h
        fill = (r, g, b) if i != active_idx else INK
        draw.rectangle([bx0, by0, bx0 + bar_w * 0.72, by1], fill=fill)


def _compose_card(map_rgba: Image.Image, year: int, month: int, day_idx: int, land_means: list[float]) -> Image.Image:
    canvas = Image.new("RGB", (CANVAS_W, CANVAS_H), WHITE)
    map_with_shadow = _compose_on_canvas(map_rgba)
    canvas.paste(map_with_shadow, (PANEL_W, 0))

    draw = ImageDraw.Draw(canvas)
    f_eyebrow = _font("arialbd.ttf", 13)
    f_title = _font("arial.ttf", 30)
    f_sub = _font("arial.ttf", 14)
    f_day = _font("arialbd.ttf", 12)
    f_date = _font("arial.ttf", 38)
    f_mean = _font("arial.ttf", 15)
    f_label = _font("arialbd.ttf", 12)
    f_note = _font("arial.ttf", 11)
    f_legend = _font("arial.ttf", 11)
    f_attr = _font("arial.ttf", 10)

    pad = 38
    y = 36
    draw.text((pad, y), f"COLOMBIA / {MESES_ES[month].upper()} {year}", font=f_eyebrow, fill=INK)
    y += 26
    draw.text((pad, y), "El calor del mes,", font=f_title, fill=INK)
    y += 38
    draw.text((pad, y), "dia a dia", font=f_title, fill=INK)
    y += 48
    draw.text((pad, y), "Temperatura media diaria a 2 m menos la normal", font=f_sub, fill=MUTED)
    y += 18
    draw.text((pad, y), "1991-2020 para ese dia", font=f_sub, fill=MUTED)

    y += 42
    the_date = date(year, month, day_idx + 1)
    weekday = DIAS_ES[the_date.weekday()]
    draw.text((pad, y), weekday, font=f_day, fill=FAINT)
    y += 20
    draw.text((pad, y), f"{day_idx + 1} de {MESES_ES[month]}", font=f_date, fill=INK)
    y += 48
    mean_val = land_means[day_idx]
    draw.text((pad, y), f"media terrestre {mean_val:+.1f} °C", font=f_mean, fill=INK)

    y += 34
    draw.text((pad, y), "MEDIA DIARIA EN TIERRA", font=f_label, fill=FAINT)
    y += 20
    spark_w, spark_h = PANEL_W - 2 * pad, 56
    _draw_sparkline(draw, pad, y, spark_w, spark_h, land_means, day_idx)
    y += spark_h + 6
    n_days = len(land_means)
    for tick in (0, 7, 14, 21, n_days - 1):
        tx = pad + (tick / (n_days - 1)) * spark_w
        draw.text((tx - 4, y), str(tick + 1), font=f_legend, fill=FAINT)

    y += 30
    draw.text((pad, y), "La altura y el color muestran la misma anomalia.", font=f_note, fill=INK)
    y += 15
    draw.text((pad, y), "Calido sube; frio se hunde. No es elevacion real.", font=f_note, fill=MUTED)

    y += 34
    bar_w = PANEL_W - 2 * pad
    bar_h = 12
    for i in range(bar_w):
        t = i / (bar_w - 1)
        r, g, b = (int(c * 255) for c in CMAP(t)[:3])
        draw.line([(pad + i, y), (pad + i, y + bar_h)], fill=(r, g, b))
    y += bar_h + 16
    for frac, label in ((0.0, "\u2264 -9 °C"), (0.25, "-4.5"), (0.5, "0"), (0.75, "+4.5"), (1.0, "\u2265 +9 °C")):
        tx = pad + frac * bar_w
        anchor = "la" if frac == 0 else ("ra" if frac == 1 else "ma")
        draw.text((tx, y), label, font=f_legend, fill=FAINT, anchor=anchor)

    attr_y = CANVAS_H - 118
    draw.text((pad, attr_y), "ERA5-Land, medias diarias · Copernicus / ECMWF", font=f_attr, fill=FAINT)
    draw.text((pad, attr_y + 14), "Normal: medias mensuales 1991-2020, por dia", font=f_attr, fill=FAINT)
    draw.text((pad, attr_y + 28), "Natural Earth · forge3d-colombia, render 3D GPU", font=f_attr, fill=FAINT)
    draw.text((pad, attr_y + 42), "Inspirado en 'A warmer shape of summer' de Milos Popovic", font=f_attr, fill=FAINT)

    f_sig = _font("arialbd.ttf", 13)
    f_sig_link = _font("arial.ttf", 12)
    draw.text((pad, attr_y + 68), SIGNATURE_NAME, font=f_sig, fill=INK)
    draw.text((pad, attr_y + 86), SIGNATURE_LINKEDIN, font=f_sig_link, fill=MUTED)

    return canvas


def render_all_days(stack: np.ndarray) -> tuple[list[Image.Image], list[float]]:
    tmp_path = OUT_DIR / "_tmp_shade.png"
    maps, land_means = [], []
    for i in range(stack.shape[0]):
        day = stack[i]
        maps.append(_render_map_rgba(day, tmp_path))
        land_means.append(float(np.nanmean(day)))
        print(f"[render] dia {i + 1:02d}/{stack.shape[0]} listo")
    tmp_path.unlink(missing_ok=True)
    return maps, land_means


def render_grid(maps: list[Image.Image], land_means: list[float], year: int, month: int) -> Path:
    n_days = len(maps)
    n_cols = 6
    n_rows = int(np.ceil(n_days / n_cols))
    cell = RENDER_PX // 3
    margin, label_h = 4, 20

    grid_img = Image.new(
        "RGB",
        (n_cols * (cell + margin) + margin, n_rows * (cell + label_h + margin) + 70),
        WHITE,
    )
    draw = ImageDraw.Draw(grid_img)
    f_title = _font("arial.ttf", 22)
    f_sub = _font("arial.ttf", 12)
    f_day = _font("arial.ttf", 11)

    month_name = MESES_ES[month]
    draw.text((margin, 12), f"Anomalia de temperatura en Colombia — {month_name} {year}", font=f_title, fill=INK)
    draw.text((margin, 40), "ERA5-Land · media diaria vs. normal 1991-2020 · render 3D forge3d-colombia", font=f_sub, fill=MUTED)

    for i, map_rgba in enumerate(maps):
        row, col = divmod(i, n_cols)
        thumb = _compose_on_canvas(map_rgba).resize((cell, cell), Image.LANCZOS)
        x = margin + col * (cell + margin)
        y = 70 + row * (cell + label_h + margin)
        grid_img.paste(thumb, (x, y))
        draw.text((x, y + cell + 2), f"{i + 1:02d}  {land_means[i]:+.1f}°C", font=f_day, fill=MUTED)

    out_path = OUT_DIR / f"colombia_temp_anomaly_{year:04d}{month:02d}_grid.png"
    grid_img.save(out_path)
    print(f"[ok] mosaico guardado en {out_path}")
    return out_path


def render_gif(maps: list[Image.Image], land_means: list[float], year: int, month: int) -> Path:
    import imageio.v2 as imageio

    cards = [
        np.asarray(_compose_card(m, year, month, i, land_means))
        for i, m in enumerate(maps)
    ]
    out_path = OUT_DIR / f"colombia_temp_anomaly_{year:04d}{month:02d}.gif"
    imageio.mimsave(out_path, cards, duration=0.35, loop=0)
    print(f"[ok] gif guardado en {out_path}")
    return out_path


def render_video(
    maps: list[Image.Image], land_means: list[float], year: int, month: int,
    fps: int = 24, hold_frames: int = 16, transition_frames: int = 8,
) -> Path:
    """Video con una pausa por dia (para poder leer la tarjeta) seguida de
    una transicion suave al dia siguiente — ritmo similar al original
    (~1 s por dia, ~30 s en total para un mes de 30 dias)."""
    import imageio.v2 as imageio

    n_days = len(maps)
    cards = [
        np.asarray(_compose_card(m, year, month, i, land_means)).astype(np.float32)
        for i, m in enumerate(maps)
    ]

    out_path = OUT_DIR / f"colombia_temp_anomaly_{year:04d}{month:02d}.mp4"
    writer = imageio.get_writer(out_path, fps=fps, codec="libx264", quality=8)
    try:
        for day in range(n_days):
            current = cards[day].astype(np.uint8)
            for _ in range(hold_frames):
                writer.append_data(current)
            if day + 1 < n_days:
                end = cards[day + 1]
                for s in range(1, transition_frames + 1):
                    t = s / transition_frames
                    blended = np.clip(cards[day] * (1 - t) + end * t, 0, 255).astype(np.uint8)
                    writer.append_data(blended)
        last = cards[-1].astype(np.uint8)
        for _ in range(fps):
            writer.append_data(last)
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

    maps, land_means = render_all_days(stack)
    render_grid(maps, land_means, year, month)
    render_gif(maps, land_means, year, month)
    render_video(maps, land_means, year, month)


if __name__ == "__main__":
    main()
