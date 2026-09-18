"""
Whether a newer release than the one running exists on GitHub.

Asked for by the navbar on every page load, so the answer is fetched once and reused:
GitHub allows 60 unauthenticated requests an hour per address, and an instance serving
several editors would burn through that in a morning otherwise.
"""

import asyncio
import re
import time

from config import (
    APP_VERSION,
    UPDATE_CHECK_REPO,
    UPDATE_CHECK_RETRY_TTL,
    UPDATE_CHECK_TTL,
)
from utils import HTTP

# A release this understands is digits and dots, optionally with a leading v. Anything
# else - a pre-release, a date-stamped tag, a name rather than a number - is left alone
# rather than guessed at, so nobody is told to update to something unintelligible.
_VERSION_RE = re.compile(r'^v?(\d+(?:\.\d+)*)$')


def parse_version(text: str | None) -> tuple[int, ...] | None:
    if not text:
        return None
    match = _VERSION_RE.match(text.strip())
    if match is None:
        return None
    return tuple(int(part) for part in match[1].split('.'))


def is_newer(latest: str | None, current: str | None) -> bool:
    """True only when both versions are readable and latest is genuinely ahead."""
    latest_parts = parse_version(latest)
    current_parts = parse_version(current)
    if latest_parts is None or current_parts is None:
        return False

    # 1.2 and 1.2.0 are the same release written two ways
    length = max(len(latest_parts), len(current_parts))
    pad = lambda parts: parts + (0,) * (length - len(parts))  # noqa: E731
    return pad(latest_parts) > pad(current_parts)


_lock = asyncio.Lock()
_cached: dict | None = None
_cached_until = 0.0


async def _fetch_latest_release() -> dict | None:
    r = await HTTP.get(
        f'https://api.github.com/repos/{UPDATE_CHECK_REPO}/releases/latest',
        headers={'Accept': 'application/vnd.github+json'},
        timeout=10,
        follow_redirects=True,
    )
    # a repository with no releases yet answers 404, which is an answer, not a failure
    if r.status_code == 404:
        return None
    r.raise_for_status()
    data = r.json()
    return {
        'version': str(data.get('tag_name') or '').lstrip('vV'),
        'url': str(data.get('html_url') or ''),
    }


async def get_latest_release() -> dict | None:
    """
    The newest published release, or None when there is nothing to compare against.

    Never raises: an instance behind a firewall, or one GitHub is rate-limiting, shows
    its version without an update notice rather than an error.
    """
    global _cached, _cached_until

    if not UPDATE_CHECK_REPO:
        return None

    async with _lock:
        if time.monotonic() < _cached_until:
            return _cached

        try:
            _cached = await _fetch_latest_release()
            _cached_until = time.monotonic() + UPDATE_CHECK_TTL
        except Exception as e:
            print(f'[UPDATE] Could not check for a newer release: {e!r}')
            # keep whatever was known before, and ask again sooner than a good answer would
            _cached_until = time.monotonic() + UPDATE_CHECK_RETRY_TTL

        return _cached


async def get_version_status() -> dict:
    latest = await get_latest_release()
    latest_version = latest['version'] if latest else None

    return {
        'version': APP_VERSION,
        'latest': latest_version,
        'updateAvailable': is_newer(latest_version, APP_VERSION),
        'url': (latest['url'] if latest else '') or f'https://github.com/{UPDATE_CHECK_REPO}/releases',
    }
