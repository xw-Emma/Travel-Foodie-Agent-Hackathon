"""Pure-Python Google encoded polyline helpers."""
from __future__ import annotations


def _encode_value(value: int) -> str:
    value = ~(value << 1) if value < 0 else value << 1
    output = []
    while value >= 0x20:
        output.append(chr((0x20 | (value & 0x1F)) + 63))
        value >>= 5
    output.append(chr(value + 63))
    return "".join(output)


def encode_polyline(points: list[tuple[float, float]]) -> str:
    """Encode ``(latitude, longitude)`` points using Google's format."""
    last_lat = 0
    last_lon = 0
    output = []
    for latitude, longitude in points:
        lat = round(latitude * 1e5)
        lon = round(longitude * 1e5)
        output.append(_encode_value(lat - last_lat))
        output.append(_encode_value(lon - last_lon))
        last_lat, last_lon = lat, lon
    return "".join(output)


def decode_polyline(encoded: str) -> list[list[float]]:
    """Decode Google's format into pydeck ``[longitude, latitude]`` paths."""
    points: list[list[float]] = []
    index = 0
    latitude = 0
    longitude = 0
    while index < len(encoded):
        values = []
        for _ in range(2):
            result = 0
            shift = 0
            while True:
                if index >= len(encoded):
                    raise ValueError("Truncated encoded polyline")
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            # Google's format stores negatives as the bitwise complement, so this
            # must be ~(result >> 1), not -(result >> 1) — they differ by one.
            # The deltas accumulate, so a one-unit error per negative delta
            # compounds along the path instead of staying a fixed offset.
            values.append(~(result >> 1) if result & 1 else result >> 1)
        latitude += values[0]
        longitude += values[1]
        points.append([longitude / 1e5, latitude / 1e5])
    return points
