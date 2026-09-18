# NaPTAN gives the direction buses travel when calling at a stop as a compass point
_COMPASS_DEGREES = {'N': 0, 'NE': 45, 'E': 90, 'SE': 135, 'S': 180, 'SW': 225, 'W': 270, 'NW': 315}

# compass points are 45° apart and roads bend, so only a clearly opposite heading counts
OPPOSITE_HEADING_ANGLE = 120  # degrees


def compass_degrees(bearing: str) -> int | None:
    return _COMPASS_DEGREES.get(bearing.strip().upper())


def angle_between(a: float, b: float) -> float:
    difference = abs(a - b) % 360
    return min(difference, 360 - difference)
