"""Shared distance maths.

Lives outside the backend modules because both of them need it and neither owns
it: the live backend turns a radius into a bounding box for the Places request,
the offline backend measures real distances, and the orchestrator scores
candidates by how far they are from the previous stop.
"""
from __future__ import annotations

import math

# Mean km per degree of latitude. Longitude shrinks by cos(latitude).
KM_PER_DEGREE = 111.32

# Straight-line distance understates a real journey through a street grid.
STREET_GRID_FACTOR = 1.4


def haversine_km(from_lat: float, from_lon: float,
                 to_lat: float, to_lon: float) -> float:
    """Great-circle km with a street-grid detour factor applied."""
    radius = 6371.0
    p1, p2 = math.radians(from_lat), math.radians(to_lat)
    dphi = math.radians(to_lat - from_lat)
    dlmb = math.radians(to_lon - from_lon)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2)
    return 2 * radius * math.asin(math.sqrt(a)) * STREET_GRID_FACTOR


def bounding_box(lat: float, lon: float, radius_km: float) -> tuple[float, float, float, float]:
    """(low_lat, low_lon, high_lat, high_lon) enclosing a circle.

    Google's Text Search only accepts a rectangle for locationRestriction - a
    circle is rejected outright - so a radius has to become a box. The corners
    reach past the radius, which is why callers still filter on true distance.
    """
    delta_lat = radius_km / KM_PER_DEGREE
    delta_lon = radius_km / (KM_PER_DEGREE * max(0.01, math.cos(math.radians(lat))))
    return (lat - delta_lat, lon - delta_lon, lat + delta_lat, lon + delta_lon)
