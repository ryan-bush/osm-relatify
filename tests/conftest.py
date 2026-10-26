import pytest

from config import OverpassInstance

_PUBLIC_INSTANCES = tuple(
    OverpassInstance(u, u)
    for u in (
        'https://overpass-api.de/api/interpreter',
        'https://maps.mail.ru/osm/tools/overpass/api/interpreter',
    )
)


@pytest.fixture(autouse=True)
def _public_overpass_instances(monkeypatch):
    """The keys in a local .env put keyed instances first; the tests expect the public ones."""
    monkeypatch.setattr('overpass.OVERPASS_INSTANCES', _PUBLIC_INSTANCES)
