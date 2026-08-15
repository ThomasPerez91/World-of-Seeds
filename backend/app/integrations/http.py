import json
from typing import Any

import httpx

MAX_INTEGRATION_RESPONSE_BYTES = 16 * 1024 * 1024


class IntegrationRequestError(Exception):
    """Raised when an integration does not return a bounded, valid response."""


class IntegrationAuthenticationError(IntegrationRequestError):
    """Raised when an integration rejects configured credentials."""


async def read_limited_json(
    response: httpx.Response,
    *,
    max_bytes: int = MAX_INTEGRATION_RESPONSE_BYTES,
) -> Any:
    body = await read_limited_bytes(response, max_bytes=max_bytes)
    try:
        return json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IntegrationRequestError("Integration returned invalid JSON") from exc


async def read_limited_text(response: httpx.Response, *, max_bytes: int) -> str:
    body = await read_limited_bytes(response, max_bytes=max_bytes)
    try:
        return body.decode(response.encoding or "utf-8")
    except UnicodeDecodeError as exc:
        raise IntegrationRequestError("Integration returned invalid text") from exc


async def read_limited_bytes(response: httpx.Response, *, max_bytes: int) -> bytes:
    content_length = response.headers.get("Content-Length")
    if content_length is not None:
        try:
            if int(content_length) > max_bytes:
                raise IntegrationRequestError("Integration response is too large")
        except ValueError as exc:
            raise IntegrationRequestError("Integration returned an invalid Content-Length") from exc

    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > max_bytes:
            raise IntegrationRequestError("Integration response is too large")
        chunks.append(chunk)

    return b"".join(chunks)


def integration_timeout(connect_seconds: float, read_seconds: float) -> httpx.Timeout:
    return httpx.Timeout(
        connect=connect_seconds,
        read=read_seconds,
        write=read_seconds,
        pool=connect_seconds,
    )
