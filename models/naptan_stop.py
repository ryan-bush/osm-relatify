from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True, slots=True)
class NaptanStop:
    """A bus stop from NaPTAN, with the tags an OSM platform made from it would carry."""

    atcoCode: str
    name: str
    indicator: str
    latLng: tuple[float, float]
    tags: dict[str, str]
