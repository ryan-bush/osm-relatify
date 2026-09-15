from dataclasses import dataclass, field

from stop_areas import NewStopAreaPlan


@dataclass(frozen=True, kw_only=True, slots=True)
class OsmChange:
    """
    A changeset document, and what the upload has to read back out of it.

    The relations a change creates carry placeholder ids until OSM assigns real ones, and
    the diffResult names them by the placeholder they went up with. Which placeholder
    belongs to what is only known here, where they were handed out.
    """

    xml: str
    # the stop areas this change creates, so the real ids can be told to the client; it
    # learns about stop areas from Overpass, which runs minutes behind
    new_stop_areas: tuple[NewStopAreaPlan, ...] = field(default_factory=tuple)
