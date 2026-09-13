from dataclasses import dataclass, field


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
    """What NaPTAN has to say about an OSM stop matched to it."""

    # the OSM platform the tags would go on
    type: str
    id: str
    atcoCode: str
    # NaPTAN's value for each tag the stop lacks, to fill in
    tags: dict[str, str]
    # NaPTAN's value for each tag the stop holds a different value for, to review.
    # The stop's own tags are already on the client, which is where the two are compared.
    differing: dict[str, str] = field(default_factory=dict)
