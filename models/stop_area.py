from dataclasses import dataclass, field


@dataclass(frozen=True, kw_only=True, slots=True)
class StopArea:
    """A public_transport=stop_area relation that some of the downloaded stops are in."""

    id: int
    name: str
    # "type/id" of every member, so the client can tell which of a group's stops are in
    # it and which are missing. Members outside the downloaded area are included too.
    members: list[str] = field(default_factory=list)


@dataclass(frozen=True, kw_only=True, slots=True)
class NewStopArea:
    """A stop area to create, or members to add to one that already exists."""

    # absent when adding to an existing relation, whose id is given instead
    name: str
    # "type/id" of each member to put in it, platforms and stop positions alike
    members: list[str] = field(default_factory=list)
