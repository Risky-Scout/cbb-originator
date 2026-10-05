"""The ONE generic network fetch interface. Every adapter that can return historical outcomes or
postgame information (CBBD, BALLDONTLIE, a future Bart Torvik adapter, anything new) calls `get`.

    get(provider, endpoint, url, params, headers) ->
        1. lockbox.check_request   (before any network traffic; unknown provider/endpoint = refused)
        2. HTTP GET
        3. status check            (error text carries provider, endpoint and status ONLY: no body,
                                    no headers, so a credential can never reach an exception or log)
        4. lockbox.check_response  (before the caller can store anything)
Returns (raw_bytes, parsed_json). Storage stays with the caller (raw_store.store).
"""
from __future__ import annotations

import json

import requests

from cbb_platform import lockbox


class SourceHTTPError(RuntimeError):
    def __init__(self, provider: str, endpoint: str, status: int):
        super().__init__(f"{provider} {endpoint} returned HTTP {status}")
        self.status = status


def get(provider: str, endpoint: str, url: str, params: dict, headers: dict, *,
        known_game_season=None, guard_params: dict | None = None, timeout: int = 60) -> tuple[bytes, object]:
    """`guard_params` = the full logical request when some parameters travel in the URL path
    (e.g. CBBD /plays/game/{gameId}); defaults to the query params."""
    lockbox.check_request(provider, endpoint, params if guard_params is None else guard_params, known_game_season)
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=timeout)
    except requests.RequestException as exc:          # never re-raise: the exception may hold the request
        raise RuntimeError(f"{provider} {endpoint} request failed: {type(exc).__name__}") from None
    if not 200 <= getattr(resp, "status_code", 200) < 300:
        raise SourceHTTPError(provider, endpoint, resp.status_code)
    body = json.loads(resp.content)
    lockbox.check_response(provider, body)
    return resp.content, body
