"""Renderiza la anomalia diaria de temperatura de Colombia (ERA5-Land) como
un relieve 3D de "agujas" al estilo del video original de Milos Popovic
sobre Europa (https://lnkd.in/p/ej6MgpQD, "A warmer shape of summer, day by
day") — ese post usa forge3d por debajo (su enlace a "la herramienta"
apunta al propio repositorio).

Pipeline (via el visor interactivo de forge3d, controlado por IPC, igual
que examples/population_spike_worldpop/*.py):
  1. La propia anomalia (celda a celda, sin suavizar) es la altura de un
     GeoTIFF que se carga como terreno — calido sube, frio se hunde. La
     resolucion nativa de ERA5-Land (~9 km) sin interpolar es lo que crea
     la textura fina de "agujas" del video original.
  2. El color es un overlay PNG con un colormap continuo muestreado pixel
     a pixel de la paleta real del video (azul grisaceo -> crema ->
     naranja -> rojo), cargado con ``preserve_colors=True`` para que el
     motor lo componga DESPUES de la iluminacion 3D (sombras/AO) en vez de
     teñirlo con el tonemap, como hace un ``RasterOverlay`` normal de
     ``MapScene`` (ver docs/guides/thematic_raster_styles.md).
  3. PBR con sombras pcss + ambient occlusion basado en altura (AO entre
     agujas vecinas) es lo que da la profundidad/textura marcada.
  4. El snapshot (ya con fondo blanco nativo) se compone con una sombra
     proyectada sintetica y el panel lateral editorial (titulo, fecha,
     media diaria, mini histograma, leyenda, atribucion, firma).

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
from datetime import date
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from matplotlib.colors import LinearSegmentedColormap
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from rasterio.mask import mask as rio_mask

REPO_ROOT_FOR_IMPORT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT_FOR_IMPORT / "python"))
import forge3d as f3d  # noqa: E402

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
VRANGE = 5.0  # anomalia en +-5 C mapea a los extremos de la paleta (el mes
# real ronda -4..+6.5 C; con +-9 los colores quedaban casi todos al centro)

RENDER_PX = 1100
PANEL_W = 430
CANVAS_W, CANVAS_H = PANEL_W + RENDER_PX, RENDER_PX

# Camara / relieve del visor interactivo (ver examples/population_spike_worldpop)
CAMERA_PHI = 78.0
CAMERA_THETA = -60.0
CAMERA_RADIUS = 340.0
CAMERA_FOV = 32.0
SUN_AZIMUTH, SUN_ELEVATION, SUN_INTENSITY, AMBIENT = 110.0, 35.0, 3.0, 0.18
ZSCALE_PEAK_FRACTION = 0.55  # pico mas alto ~= esta fraccion del ancho de la rejilla

SIGNATURE_NAME = "Jonhatan Rivadeneira Pacheco"
SIGNATURE_LINKEDIN = "linkedin.com/in/jonhatan-jose-rivadeneira-pacheco-192354130"


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


def _safe_zscale(stack: np.ndarray, grid_width: int) -> float:
    """zscale tal que el pico mas alto del mes sea ~ZSCALE_PEAK_FRACTION del
    ancho de la rejilla — mismo zscale para los 30 dias asi la escala
    vertical es comparable entre tarjetas."""
    max_abs = float(np.nanmax(np.abs(stack)))
    return ZSCALE_PEAK_FRACTION * grid_width / max_abs


def _content_alpha(snapshot_rgb: np.ndarray, bg=(255, 255, 255), threshold: int = 6) -> np.ndarray:
    """Mascara de 'no es el fondo blanco del visor' para recortar el mapa."""
    diff = np.abs(snapshot_rgb.astype(np.int16) - np.array(bg, dtype=np.int16))
    return (np.any(diff > threshold, axis=2) * 255).astype(np.uint8)


def _write_day_assets(day: np.ndarray, dem_path: Path, overlay_path: Path) -> None:
    """Escribe el GeoTIFF de altura (= anomalia, con signo) y el overlay de
    color para un dia, listos para cargar en el visor interactivo."""
    mask_valid = ~np.isnan(day)
    height_data = np.where(mask_valid, day, 0.0).astype(np.float32)
    Image.fromarray(np.ascontiguousarray(height_data)).save(
        str(dem_path), format="TIFF", compression="raw"
    )

    norm = np.clip((day + VRANGE) / (2 * VRANGE), 0.0, 1.0)
    color = (CMAP(norm)[..., :3] * 255).astype(np.uint8)
    alpha = np.where(mask_valid, 255, 0).astype(np.uint8)
    Image.fromarray(np.dstack([color, alpha]), mode="RGBA").save(str(overlay_path))


def _render_map_rgba(
    viewer, day: np.ndarray, zscale: float,
    dem_path: Path, overlay_path: Path, snap_path: Path,
) -> Image.Image:
    """Carga el relieve+overlay de un dia en el visor ya abierto y toma el
    snapshot; devuelve el recorte RGBA (alpha = contenido, no fondo)."""
    _write_day_assets(day, dem_path, overlay_path)

    viewer.load_terrain(str(dem_path))
    viewer.send_ipc({
        "cmd": "set_terrain",
        "phi": CAMERA_PHI, "theta": CAMERA_THETA, "radius": CAMERA_RADIUS, "fov": CAMERA_FOV,
        "zscale": zscale,
        "sun_azimuth": SUN_AZIMUTH, "sun_elevation": SUN_ELEVATION,
        "sun_intensity": SUN_INTENSITY, "ambient": AMBIENT,
        "background": [1.0, 1.0, 1.0],
    })
    viewer.send_ipc({
        "cmd": "set_terrain_pbr",
        "enabled": True, "exposure": 1.1,
        "shadow_technique": "pcss", "shadow_map_res": 4096, "msaa": 4,
        "normal_strength": 0.65,
        "height_ao": {
            "enabled": True, "directions": 6, "steps": 14,
            "max_distance": 60.0, "strength": 0.6, "resolution_scale": 0.5,
        },
    })
    viewer.load_overlay(name="anomaly", path=str(overlay_path.resolve()), opacity=1.0, preserve_colors=True)
    viewer.send_ipc({"cmd": "set_overlay_solid", "solid": False})

    if snap_path.exists():
        snap_path.unlink()
    viewer.snapshot(str(snap_path), width=RENDER_PX, height=RENDER_PX)

    snap_rgb = np.array(Image.open(snap_path).convert("RGB"))
    alpha = _content_alpha(snap_rgb)
    return Image.fromarray(np.dstack([snap_rgb, alpha]), mode="RGBA")


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
    f_eyebrow = _font("arialbd.ttf", 18)
    f_title = _font("arial.ttf", 44)
    f_sub = _font("arial.ttf", 19)
    f_day = _font("arialbd.ttf", 17)
    f_date = _font("arial.ttf", 52)
    f_mean = _font("arial.ttf", 22)
    f_label = _font("arialbd.ttf", 16)
    f_note = _font("arial.ttf", 15)
    f_legend = _font("arial.ttf", 14)
    f_attr = _font("arial.ttf", 13)

    pad = 38
    y = 34
    draw.text((pad, y), f"COLOMBIA / {MESES_ES[month].upper()} {year}", font=f_eyebrow, fill=INK)
    y += 32
    draw.text((pad, y), "El calor del mes,", font=f_title, fill=INK)
    y += 54
    draw.text((pad, y), "dia a dia", font=f_title, fill=INK)
    y += 62
    draw.text((pad, y), "Temperatura media diaria a 2 m menos la normal", font=f_sub, fill=MUTED)
    y += 24
    draw.text((pad, y), "1991-2020 para ese dia", font=f_sub, fill=MUTED)

    y += 40
    the_date = date(year, month, day_idx + 1)
    weekday = DIAS_ES[the_date.weekday()]
    draw.text((pad, y), weekday, font=f_day, fill=FAINT)
    y += 26
    draw.text((pad, y), f"{day_idx + 1} de {MESES_ES[month]}", font=f_date, fill=INK)
    y += 62
    mean_val = land_means[day_idx]
    draw.text((pad, y), f"media terrestre {mean_val:+.1f} °C", font=f_mean, fill=INK)

    y += 40
    draw.text((pad, y), "MEDIA DIARIA EN TIERRA", font=f_label, fill=FAINT)
    y += 24
    spark_w, spark_h = PANEL_W - 2 * pad, 64
    _draw_sparkline(draw, pad, y, spark_w, spark_h, land_means, day_idx)
    y += spark_h + 8
    n_days = len(land_means)
    for tick in (0, 7, 14, 21, n_days - 1):
        tx = pad + (tick / (n_days - 1)) * spark_w
        draw.text((tx - 6, y), str(tick + 1), font=f_legend, fill=FAINT)

    y += 34
    draw.text((pad, y), "La altura y el color muestran la misma anomalia.", font=f_note, fill=INK)
    y += 20
    draw.text((pad, y), "Calido sube; frio se hunde. No es elevacion real.", font=f_note, fill=MUTED)

    y += 36
    bar_w = PANEL_W - 2 * pad
    bar_h = 16
    for i in range(bar_w):
        t = i / (bar_w - 1)
        r, g, b = (int(c * 255) for c in CMAP(t)[:3])
        draw.line([(pad + i, y), (pad + i, y + bar_h)], fill=(r, g, b))
    y += bar_h + 20
    vr_half = VRANGE / 2
    legend_items = (
        (0.0, f"≤ -{VRANGE:.0f} °C"), (0.25, f"-{vr_half:.1f}"), (0.5, "0"),
        (0.75, f"+{vr_half:.1f}"), (1.0, f"≥ +{VRANGE:.0f} °C"),
    )
    for frac, label in legend_items:
        tx = pad + frac * bar_w
        anchor = "la" if frac == 0 else ("ra" if frac == 1 else "ma")
        draw.text((tx, y), label, font=f_legend, fill=FAINT, anchor=anchor)

    attr_y = CANVAS_H - 150
    draw.text((pad, attr_y), "ERA5-Land, medias diarias · Copernicus / ECMWF", font=f_attr, fill=FAINT)
    draw.text((pad, attr_y + 18), "Normal: medias mensuales 1991-2020, por dia", font=f_attr, fill=FAINT)
    draw.text((pad, attr_y + 36), "Natural Earth · forge3d-colombia, render 3D GPU", font=f_attr, fill=FAINT)
    draw.text((pad, attr_y + 54), "Inspirado en 'A warmer shape of summer' de Milos Popovic", font=f_attr, fill=FAINT)

    f_sig = _font("arialbd.ttf", 18)
    f_sig_link = _font("arial.ttf", 16)
    draw.text((pad, attr_y + 86), SIGNATURE_NAME, font=f_sig, fill=INK)
    draw.text((pad, attr_y + 108), SIGNATURE_LINKEDIN, font=f_sig_link, fill=MUTED)

    return canvas


def render_all_days(stack: np.ndarray, year: int, month: int, use_cache: bool = True) -> tuple[list[Image.Image], list[float]]:
    """Renderiza (o recupera de cache) el snapshot recortado de cada dia.

    El render 3D (abrir el visor, cargar terreno/overlay, pedir snapshot)
    es la parte lenta del pipeline; cachear los PNG ya compuestos en
    assets/era5/_frames_cache/<YYYYMM>/ permite reajustar el ritmo del
    video/gif o el diseño del panel sin volver a tocar el motor.
    """
    n_days = stack.shape[0]
    cache_dir = ERA5_DIR / "_frames_cache" / f"{year:04d}{month:02d}"
    cache_files = [cache_dir / f"day_{i + 1:02d}.png" for i in range(n_days)]
    means_path = cache_dir / "land_means.json"

    if use_cache and means_path.exists() and all(p.exists() for p in cache_files):
        import json
        maps = [Image.open(p).convert("RGBA") for p in cache_files]
        land_means = json.loads(means_path.read_text())
        print(f"[info] usando cache de renders en {cache_dir}")
        return maps, land_means

    zscale = _safe_zscale(stack, grid_width=stack.shape[2])
    print(f"[info] zscale calibrado a {zscale:.1f} para este mes")

    dem_path = OUT_DIR / "_tmp_dem.tif"
    overlay_path = OUT_DIR / "_tmp_overlay.png"
    snap_path = OUT_DIR / "_tmp_snap.png"
    cache_dir.mkdir(parents=True, exist_ok=True)

    maps, land_means = [], []
    with _open_viewer_with_first_day(stack[0], dem_path, overlay_path) as viewer:
        for i in range(n_days):
            day = stack[i]
            map_rgba = _render_map_rgba(viewer, day, zscale, dem_path, overlay_path, snap_path)
            maps.append(map_rgba)
            land_means.append(float(np.nanmean(day)))
            map_rgba.save(cache_files[i])
            print(f"[render] dia {i + 1:02d}/{n_days} listo")

    import json
    means_path.write_text(json.dumps(land_means))

    for p in (dem_path, overlay_path, snap_path):
        p.unlink(missing_ok=True)
    return maps, land_means


def _open_viewer_with_first_day(first_day: np.ndarray, dem_path: Path, overlay_path: Path):
    """Abre el visor interactivo con el terreno del primer dia ya cargado
    (open_viewer_async necesita un terrain_path valido al arrancar)."""
    _write_day_assets(first_day, dem_path, overlay_path)
    return f3d.open_viewer_async(terrain_path=dem_path, width=RENDER_PX, height=RENDER_PX, timeout=60)


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
    """Video con una pausa por dia seguida de una transicion al dia
    siguiente. El ritmo (~1 s por dia, ~31 s en total para un mes de 30
    dias) esta medido directamente del video original de Milos Popovic
    (924 frames a 30 fps / 30 dias): hold y transicion ocupan cada uno
    aprox. la mitad del segundo por dia, no los varios segundos que se
    probaron en iteraciones anteriores."""
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

    maps, land_means = render_all_days(stack, year, month)
    render_grid(maps, land_means, year, month)
    render_gif(maps, land_means, year, month)
    render_video(maps, land_means, year, month)


if __name__ == "__main__":
    main()
