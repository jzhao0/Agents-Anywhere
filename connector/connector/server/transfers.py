from __future__ import annotations

import asyncio

from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import httpx

from connector.logging import logger
from connector.server.auth import ConnectorAuthenticationError
from connector.server.errors import ConnectorNetworkError
from connector.server.urls import api_v2_url

AccessTokenProvider = Callable[..., Any]
HttpClientFactory = Callable[[httpx.Timeout | float], httpx.AsyncClient]


async def _get_attachment_with_network_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str],
    file_id: str,
    attempts: int = 3,
) -> httpx.Response:
    """Retry only transport failures for the idempotent attachment GET.

    The retry stays inside the already-created session, before runtime/model
    execution. HTTP responses are returned to the caller unchanged so auth
    refresh and non-2xx status handling keep their existing semantics.
    """
    last_error: httpx.RequestError | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await client.get(url, headers=headers)
        except httpx.RequestError as exc:
            last_error = exc
            if attempt >= attempts:
                break
            logger.warning(
                "attachment download transport retry file_id={} attempt={}/{} error_type={}",
                file_id,
                attempt,
                attempts,
                type(exc).__name__,
            )
            await asyncio.sleep(0.1 * (2 ** (attempt - 1)))
    assert last_error is not None
    raise ConnectorNetworkError(
        f"attachment download failed file_id={file_id} after {attempts} attempts"
    ) from last_error


async def download_attachment(
    server_url: str,
    session_id: str,
    file_id: str,
    access_token_provider: AccessTokenProvider,
    http_client_factory: HttpClientFactory,
) -> tuple[bytes, str, str]:
    """Pull a user-uploaded attachment by session_id and file_id."""
    access_token = await access_token_provider()
    timeout = httpx.Timeout(300.0, connect=30.0)
    target = api_v2_url(
        server_url,
        f"/connector/sessions/{session_id}/attachments/{file_id}/content",
    )
    async with http_client_factory(timeout) as client:
        response = await _get_attachment_with_network_retry(
            client,
            target,
            headers={"Authorization": f"Bearer {access_token}"},
            file_id=file_id,
        )
        if getattr(response, "status_code", None) == 401:
            access_token = await access_token_provider(force=True)
            response = await _get_attachment_with_network_retry(
                client,
                target,
                headers={"Authorization": f"Bearer {access_token}"},
                file_id=file_id,
            )
            if getattr(response, "status_code", None) == 401:
                raise ConnectorAuthenticationError(
                    "connector credential no longer valid"
                )
        response.raise_for_status()
        name = response.headers.get("X-File-Name") or file_id
        media_type = response.headers.get("Content-Type") or "application/octet-stream"
        logger.info(
            "downloaded user attachment file_id={} size={} mediaType={}",
            file_id,
            len(response.content),
            media_type,
        )
        return response.content, name, media_type


async def upload_prepared_download(
    server_url: str,
    prepared_path: str,
    params: dict[str, Any],
    access_token_provider: AccessTokenProvider,
    http_client_factory: HttpClientFactory,
) -> dict[str, Any]:
    transfer_id = params.get("transferId")
    token = params.get("token")
    upload_url = params.get("uploadUrl")
    if not isinstance(transfer_id, str) or not transfer_id:
        raise ValueError("transferId is required")
    if not isinstance(token, str) or not token:
        raise ValueError("token is required")
    if not isinstance(upload_url, str) or not upload_url:
        raise ValueError("uploadUrl is required")
    path = Path(prepared_path)
    if not path.is_file():
        raise FileNotFoundError(f"file not found: {path}")
    access_token = await access_token_provider()
    timeout = httpx.Timeout(300.0, connect=30.0)
    target = api_v2_url(server_url, upload_url)
    headers = {"Authorization": f"Bearer {access_token}"}
    params_query = {"token": token}
    async with http_client_factory(timeout) as client:
        try:
            response = await client.put(
                target,
                params=params_query,
                headers=headers,
                content=file_chunks(path),
            )
        except httpx.RequestError as exc:
            raise ConnectorNetworkError(
                f"prepared download upload failed transfer_id={transfer_id}: {exc}"
            ) from exc
        if getattr(response, "status_code", None) == 401:
            access_token = await access_token_provider(force=True)
            headers = {"Authorization": f"Bearer {access_token}"}
            try:
                response = await client.put(
                    target,
                    params=params_query,
                    headers=headers,
                    content=file_chunks(path),
                )
            except httpx.RequestError as exc:
                raise ConnectorNetworkError(
                    f"prepared download upload retry failed transfer_id={transfer_id}: {exc}"
                ) from exc
            if getattr(response, "status_code", None) == 401:
                raise ConnectorAuthenticationError(
                    "connector credential no longer valid"
                )
        response.raise_for_status()
    return {"transferId": transfer_id, "uploaded": True}


async def file_chunks(
    path: Path, chunk_size: int = 1024 * 1024
) -> AsyncIterator[bytes]:
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            yield chunk
