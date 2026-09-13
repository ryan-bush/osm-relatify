from dataclasses import dataclass, field


@dataclass(frozen=True, kw_only=True, slots=True)
class RouteMasterRoute:
    """A route relation that is a member of a route master."""

    id: int
    ref: str
    name: str


@dataclass(frozen=True, kw_only=True, slots=True)
class RouteMaster:
    """A type=route_master relation, holding the route variants of one line."""

    id: int
    tags: dict[str, str]
    # "type/id" of every member, in order. A route master should hold nothing but route
    # relations, and one that holds something else is a thing to show rather than hide.
    members: list[str] = field(default_factory=list)
    # what the member relations are, when they were looked up; empty when they were not
    routes: list[RouteMasterRoute] = field(default_factory=list)
