class RelationPlaceholders:
    """
    The negative ids relations being created carry inside a changeset.

    A changeset can create several relations at once — the route, the stop areas its stops
    are grouped into, the route master it joins — and they refer to each other by these
    ids until OSM assigns real ones. Two of them sharing an id is not a thing the API
    reports as such: it takes the members as written, and the change lands wrong. So they
    are handed out from one place rather than counted from a constant per kind.

    Ways and nodes are numbered in their own element types, and are not part of this.
    """

    # the route the mapper is editing, which is the relation everything else points at
    ROUTE = -1

    def __init__(self):
        self._next = self.ROUTE - 1

    def take(self) -> int:
        taken = self._next
        self._next -= 1
        return taken
