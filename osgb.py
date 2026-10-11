from math import atan2, cos, degrees, radians, sin, sqrt, tan

# The Ordnance Survey's own method, from "A Guide to Coordinate Systems in Great Britain":
# the British National Grid back to OSGB36 latitude and longitude on the Airy ellipsoid,
# then a Helmert shift onto WGS84. Good to about 5 m, well within NaPTAN's own error.

# Airy 1830
_AIRY_A = 6_377_563.396
_AIRY_B = 6_356_256.909
# GRS80, which WGS84 matches closely enough here
_WGS84_A = 6_378_137.0
_WGS84_B = 6_356_752.3141

# the National Grid projection
_F0 = 0.9996012717
_LAT0 = radians(49)
_LON0 = radians(-2)
_E0 = 400_000
_N0 = -100_000

# OSGB36 to WGS84
_TX, _TY, _TZ = 446.448, -125.157, 542.060  # meters
_S = 20.4894e-6  # scale, ppm
_RX, _RY, _RZ = (radians(s / 3600) for s in (0.1502, 0.2470, 0.8421))


def _meridional_arc(lat: float) -> float:
    n = (_AIRY_A - _AIRY_B) / (_AIRY_A + _AIRY_B)
    d, s = lat - _LAT0, lat + _LAT0
    return (
        _AIRY_B
        * _F0
        * (
            (1 + n + 5 / 4 * n**2 + 5 / 4 * n**3) * d
            - (3 * n + 3 * n**2 + 21 / 8 * n**3) * sin(d) * cos(s)
            + (15 / 8 * n**2 + 15 / 8 * n**3) * sin(2 * d) * cos(2 * s)
            - 35 / 24 * n**3 * sin(3 * d) * cos(3 * s)
        )
    )


def _grid_to_osgb36(easting: float, northing: float) -> tuple[float, float]:
    a, b = _AIRY_A, _AIRY_B
    e2 = 1 - b**2 / a**2

    lat = _LAT0
    m = 0.0
    while abs(northing - _N0 - m) >= 0.00001:
        lat += (northing - _N0 - m) / (a * _F0)
        m = _meridional_arc(lat)

    sin_lat, cos_lat, tan_lat = sin(lat), cos(lat), tan(lat)
    nu = a * _F0 / sqrt(1 - e2 * sin_lat**2)
    rho = a * _F0 * (1 - e2) / (1 - e2 * sin_lat**2) ** 1.5
    eta2 = nu / rho - 1
    sec_lat = 1 / cos_lat

    vii = tan_lat / (2 * rho * nu)
    viii = tan_lat / (24 * rho * nu**3) * (5 + 3 * tan_lat**2 + eta2 - 9 * tan_lat**2 * eta2)
    ix = tan_lat / (720 * rho * nu**5) * (61 + 90 * tan_lat**2 + 45 * tan_lat**4)
    x = sec_lat / nu
    xi = sec_lat / (6 * nu**3) * (nu / rho + 2 * tan_lat**2)
    xii = sec_lat / (120 * nu**5) * (5 + 28 * tan_lat**2 + 24 * tan_lat**4)
    xiia = sec_lat / (5040 * nu**7) * (61 + 662 * tan_lat**2 + 1320 * tan_lat**4 + 720 * tan_lat**6)

    de = easting - _E0
    return (
        lat - vii * de**2 + viii * de**4 - ix * de**6,
        _LON0 + x * de - xi * de**3 + xii * de**5 - xiia * de**7,
    )


def grid_to_lat_lng(easting: float, northing: float) -> tuple[float, float]:
    """A British National Grid reference as WGS84 latitude and longitude, in degrees."""
    lat, lon = _grid_to_osgb36(easting, northing)

    # onto the Airy ellipsoid in cartesian coordinates
    e2 = 1 - _AIRY_B**2 / _AIRY_A**2
    nu = _AIRY_A / sqrt(1 - e2 * sin(lat) ** 2)
    x1 = nu * cos(lat) * cos(lon)
    y1 = nu * cos(lat) * sin(lon)
    z1 = (1 - e2) * nu * sin(lat)

    # shifted onto WGS84
    s = 1 + _S
    x2 = _TX + s * x1 - _RZ * y1 + _RY * z1
    y2 = _TY + _RZ * x1 + s * y1 - _RX * z1
    z2 = _TZ - _RY * x1 + _RX * y1 + s * z1

    # and back to latitude and longitude there
    e2 = 1 - _WGS84_B**2 / _WGS84_A**2
    p = sqrt(x2**2 + y2**2)
    lat = atan2(z2, p * (1 - e2))
    for _ in range(10):
        nu = _WGS84_A / sqrt(1 - e2 * sin(lat) ** 2)
        lat = atan2(z2 + e2 * nu * sin(lat), p)

    return degrees(lat), degrees(atan2(y2, x2))
