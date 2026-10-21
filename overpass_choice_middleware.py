from http.cookies import CookieError, SimpleCookie
from urllib.parse import unquote

from starlette.types import ASGIApp, Receive, Scope, Send

from config import OVERPASS_INSTANCE_COOKIE
from overpass import OVERPASS_CHOICE


class OverpassChoiceMiddleware:
    """
    Makes the instance picked in the navbar the one this request's Overpass queries use.

    Plain ASGI rather than a FastAPI dependency, so it covers the route calculation's
    websocket as well as every endpoint without each one having to ask for it.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] not in ('http', 'websocket'):
            await self.app(scope, receive, send)
            return

        token = OVERPASS_CHOICE.set(_cookie(scope))
        try:
            await self.app(scope, receive, send)
        finally:
            OVERPASS_CHOICE.reset(token)


def _cookie(scope: Scope) -> str | None:
    for key, value in scope['headers']:
        if key == b'cookie':
            try:
                morsel = SimpleCookie(value.decode('latin-1')).get(OVERPASS_INSTANCE_COOKIE)
            except CookieError:
                continue
            if morsel is not None:
                return unquote(morsel.value)
    return None
