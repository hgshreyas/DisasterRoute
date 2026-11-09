"""Where the SIMULATED hazards sit in Bengaluru.

!! Coordinates are APPROXIMATE (from general knowledge, not surveyed). Open
!! data/scenario_preview.html after running make_scenarios.py, check each circle against the
!! real place, and edit lat/lon/radius here. Re-run the script to regenerate.

All hazards are simulated scenarios. They are not forecasts and not real incident data.
"""

# name, lat, lon, radius_m (outer radius of the hazard footprint)
FLOOD_SITES = [
    ("Silk Board junction", 12.9177, 77.6226, 800),
    ("Koramangala valley", 12.9350, 77.6180, 700),
    ("Bellandur", 12.9300, 77.6760, 1000),
    ("Hebbal", 13.0358, 77.5970, 800),
]

# Hotspot (fire) and the smoke centre, which is offset downwind of it.
WILDFIRE = {
    "hotspot": ("Bannerghatta fringe fire", 12.8450, 77.5800, 500),
    "smoke": ("Smoke plume", 12.8600, 77.5950),   # centre of the plume
    "smoke_radii_m": {"heavy": 1200, "moderate": 2300, "light": 3500},
}

# Synthetic slope-risk zones (Bengaluru has no true landslide belt; these are simulated).
LANDSLIDE_SITES = [
    ("Turahalli slope zone (synthetic)", 12.9000, 77.5150, 1200),
    ("Yelahanka slope zone (synthetic)", 13.1000, 77.5900, 1000),
]
LANDSLIDE_BLOCKS = [
    ("Turahalli road blockage", 12.9030, 77.5190, 150),
]

# Flyovers / bridges damaged in the earthquake scenario.
QUAKE_DAMAGE = [
    ("Silk Board flyover", 12.9177, 77.6226, 150, "closed"),
    ("Hebbal flyover", 13.0358, 77.5970, 150, "closed"),
    ("KR Puram / Tin Factory bridge", 13.0000, 77.6690, 150, "partial"),
]
