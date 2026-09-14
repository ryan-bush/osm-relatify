"""What kind of route a relation is, as this application reads it."""


def get_route_type(tags: dict[str, str]) -> str | None:
    if tags.get('public_transport:version') != '2':
        return None
    type = tags.get('type')
    if type not in {'route', 'disused:route', 'was:route'}:
        return None
    type_specifier = tags.get(type)
    if type_specifier == 'trolleybus':
        return 'bus'
    if type_specifier not in {'bus', 'tram'}:
        return None
    return type_specifier


def get_route_value(tags: dict[str, str]) -> str:
    """
    The kind of route as it is actually tagged: bus, tram or trolleybus.

    get_route_type() reads a trolleybus route as a bus one, which is right for routing but
    wrong for finding its siblings: a trolleybus route's master holds trolleybus routes.
    """
    return tags.get(tags.get('type', ''), '')
