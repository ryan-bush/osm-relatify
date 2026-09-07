import os
import re
import ssl
import time
from contextlib import contextmanager

import certifi
from httpx import AsyncClient, AsyncHTTPTransport

from config import USER_AGENT

# the nix shell exports SSL_CERT_FILE; outside of it (plain venv) fall back to certifi,
# as Homebrew Python has no usable default trust store
_SSL_CONTEXT = ssl.create_default_context(cafile=os.getenv('SSL_CERT_FILE') or certifi.where())


def get_http_client(base_url: str = '', *, headers: dict | None = None) -> AsyncClient:
    if headers is None:
        headers = {}
    return AsyncClient(
        base_url=base_url,
        follow_redirects=True,
        timeout=30,
        headers={'User-Agent': USER_AGENT, **headers},
        # retries transparently recover from connection-establishment failures,
        # which happen regularly with the public Overpass instances
        transport=AsyncHTTPTransport(verify=_SSL_CONTEXT, retries=3),
    )


HTTP = get_http_client()


@contextmanager
def print_run_time(message: str | list):
    start_time = time.monotonic()
    try:
        yield
    finally:
        end_time = time.monotonic()
        elapsed_time = end_time - start_time

        # support message by reference
        if isinstance(message, list):
            message = message[0]

        print(f'[⏱️] {message} took {elapsed_time:.3f}s')


def ensure_list(obj: dict | list[dict]) -> list[dict]:
    if isinstance(obj, list):
        return obj
    else:
        return [obj]


def normalize_name(
    name: str,
    *,
    lower: bool = False,
    number: bool = False,
    special: bool = False,
    whitespace: bool = False,
) -> str:
    if lower:
        name = name.lower()
    if number:
        name = re.sub(r'\b(\d\d)\b', r'0\1', name)
        name = re.sub(r'\b(\d)\b', r'00\1', name)
    if special:
        name = re.sub(r'[^\w\s]', '', name)
    if whitespace:
        name = re.sub(r'\s+', ' ', name).strip()
    return name


def extract_numbers(text: str) -> set[int]:
    return {int(n) for n in re.findall(r'\d+', text)}
