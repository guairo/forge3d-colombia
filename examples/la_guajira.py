"""Render path-traced de la Serrania de la Macuira, La Guajira (Colombia).

Analogo al ejemplo "sixty seconds to a render" de forge3d, pero usando el
DEM local de La Guajira en lugar de un dataset remoto de EE. UU./Europa.

Requiere una GPU compatible con WebGPU. Ejecutar con:
    python examples/la_guajira.py
"""

import forge3d as f3d

from forge3d_colombia.datasets import fetch_dem

dem_path = fetch_dem("la-guajira")

with f3d.open_viewer_async(terrain_path=dem_path, width=1440, height=900) as viewer:
    viewer.set_z_scale(2.0)
    # Vista orbital apuntando a la Serrania de la Macuira, la "isla" de bosque
    # nuboso que se alza sobre el desierto de La Guajira. El DEM esta en UTM
    # 19N (metros), por eso el radio de camara se expresa tambien en metros.
    viewer.set_orbit_camera(phi_deg=35, theta_deg=55, radius=18000, fov_deg=42)
    # Sol bajo resaltando el relieve de la serrania contra el desierto plano.
    viewer.set_sun(azimuth_deg=280, elevation_deg=18)
    viewer.snapshot("la_guajira.png", width=1920, height=1080)

print("Render guardado en la_guajira.png")
