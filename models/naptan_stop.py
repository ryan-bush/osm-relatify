from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True, slots=True)
class NaptanStop:
    """A bus stop from NaPTAN, with the tags an OSM platform made from it would carry."""

    atcoCode: str
    name: str
    indicator: str
    latLng: tuple[float, float]
    tags: dict[str, str]


@dataclass(frozen=True, kw_only=True, slots=True)
class NaptanTagSuggestion:
    """Tags from NaPTAN that an OSM stop matched to it is missing."""

    # the OSM platform the tags would go on
    type: str
    id: str
    atcoCode: str
    tags: dict[str, str]
