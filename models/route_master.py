from dataclasses import dataclass, field


@dataclass(frozen=True, kw_only=True, slots=True)
class RouteMasterRoute:
    """A route relation that is a member of a route master."""

    id: int
    ref: str
    name: str
    # every tag it has, so a variant can be compared with its master and with the others
    tags: dict[str, str] = field(default_factory=dict)
    # whether this application can open it: a master may hold a route tagged in a way it
    # does not read, and that is a thing to say rather than an Edit button that fails
    editable: bool = False
    # Whether OSM said what this member is at all. A member the lookup did not cover is
    # not a member that cannot be opened: the relation may have been deleted since, the
    # master may hold more than is worth expanding, or the lookup may simply have failed.
    # Saying "not a route this application can open" to any of those is saying something
    # untrue, and hiding a failure that a reload would put right.
    described: bool = False


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


@dataclass(frozen=True, kw_only=True, slots=True)
class RouteMasterView:
    """
    A route master and the routes in it, for choosing which one to edit.

    What comes back when a route master's id is given to the application instead of a
    route's. Nothing is downloaded for it: the variants are listed, and the one picked is
    loaded the way any route is.
    """

    # tells this apart from a FetchRelation, the two being answers to the same request
    kind: str = 'route_master'
    id: int = 0
    tags: dict[str, str] = field(default_factory=dict)
    routes: list[RouteMasterRoute] = field(default_factory=list)
    # "type/id" of any member that is not a relation, which a master should not hold
    otherMembers: list[str] = field(default_factory=list)
