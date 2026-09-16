from collections.abc import Iterable
from typing import Literal

DrivingSide = Literal['left', 'right']

# The countries and territories where traffic keeps left, by ISO 3166-1 code, for a
# country boundary that does not say so itself with driving_side.
LEFT_HAND_TRAFFIC = frozenset(
    {
        'AG', 'AI', 'AU', 'BB', 'BD', 'BM', 'BN', 'BS', 'BT', 'BW', 'CC', 'CK', 'CX', 'CY',
        'DM', 'FJ', 'FK', 'GB', 'GD', 'GG', 'GS', 'GY', 'HK', 'ID', 'IE', 'IM', 'IN', 'IO',
        'JE', 'JM', 'JP', 'KE', 'KI', 'KN', 'KY', 'LC', 'LK', 'LS', 'MO', 'MS', 'MT', 'MU',
        'MV', 'MW', 'MY', 'MZ', 'NA', 'NF', 'NP', 'NR', 'NU', 'NZ', 'PG', 'PK', 'PN', 'SB',
        'SC', 'SG', 'SH', 'SR', 'SZ', 'TC', 'TH', 'TK', 'TL', 'TO', 'TT', 'TV', 'TZ', 'UG',
        'VC', 'VG', 'VI', 'WS', 'ZA', 'ZM', 'ZW',
    }
)  # fmt: skip


def build_driving_side_query(lat: float, lon: float, timeout: int) -> str:
    """The country boundaries around a point."""
    return f'[out:json][timeout:{timeout}];is_in({lat},{lon})->.a;area.a[admin_level=2];out tags;'


def parse_driving_side(elements: Iterable[dict]) -> DrivingSide | None:
    """Which side traffic keeps to in the country these boundaries belong to, if any says."""
    for element in elements:
        tags = element.get('tags', {})

        if (side := tags.get('driving_side')) in ('left', 'right'):
            return side

        if code := (tags.get('ISO3166-1:alpha2') or tags.get('ISO3166-1', '')).strip().upper():
            return 'left' if code in LEFT_HAND_TRAFFIC else 'right'

    return None
