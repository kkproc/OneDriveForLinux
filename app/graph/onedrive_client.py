"""Graph API client for OneDrive operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncGenerator, Awaitable, Callable, Dict, List, Optional
from pathlib import Path

import httpx


class GraphApiError(RuntimeError):
    def __init__(self, status_code: int, payload: Dict[str, Any]) -> None:
        self.status_code = status_code
        self.payload = payload
        message = payload.get("error", {}).get("message", "Graph API request failed")
        super().__init__(f"{status_code}: {message}")


@dataclass(slots=True)
class DriveItem:
    id: str
    name: str
    is_folder: bool
    size: int
    parent_reference: Dict[str, Any]
    web_url: str
    last_modified: str
    e_tag: Optional[str] = None


TokenProvider = Callable[[], Awaitable[str]]


class OneDriveClient:
    def __init__(self, access_token_provider: TokenProvider, base_url: str = "https://graph.microsoft.com/v1.0") -> None:
        self._provider = access_token_provider
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, url: str, **kwargs: Any) -> Dict[str, Any]:
        token = await self._provider()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"
        response = await self._client.request(method, url, headers=headers, **kwargs)
        if response.status_code >= 400:
            payload = response.json()
            raise GraphApiError(response.status_code, payload)
        if response.status_code == 204:
            return {}
        return response.json()

    async def get_drive_root(self, drive_id: Optional[str] = None) -> DriveItem:
        url = f"/drives/{drive_id}/root" if drive_id else "/me/drive/root"
        payload = await self._request("GET", url)
        return self._to_drive_item(payload)

    async def list_children(
        self,
        item_id: str,
        drive_id: Optional[str] = None,
        page_size: int = 200,
    ) -> AsyncGenerator[List[DriveItem], None]:
        if drive_id:
            url = f"/drives/{drive_id}/items/{item_id}/children"
        else:
            url = f"/me/drive/items/{item_id}/children"

        params = {"$top": page_size}
        while url:
            payload = await self._request("GET", url, params=params)
            items = [self._to_drive_item(entry) for entry in payload.get("value", [])]
            yield items
            url = payload.get("@odata.nextLink")
            params = None

    async def delta(self, item_id: str, delta_link: Optional[str] = None) -> Dict[str, Any]:
        if delta_link:
            url = delta_link
        else:
            url = f"/me/drive/items/{item_id}/delta"
        return await self._request("GET", url)

    async def download(self, item_id: str, drive_id: Optional[str] = None) -> bytes:
        if drive_id:
            url = f"/drives/{drive_id}/items/{item_id}/content"
        else:
            url = f"/me/drive/items/{item_id}/content"
        response = await self._client.get(
            url,
            headers={"Authorization": f"Bearer {await self._provider()}"},
            follow_redirects=True,
        )
        if response.status_code >= 400:
            raise GraphApiError(response.status_code, response.json())
        return await response.aread()

    async def upload_item(self, folder_remote_id: str, local_path: Path, relative: Path) -> DriveItem:
        relative_url = relative.as_posix().lstrip("/")
        url = f"/me/drive/items/{folder_remote_id}:/{relative_url}:/$value" if relative_url else f"/me/drive/items/{folder_remote_id}/content"
        token = await self._provider()
        with open(local_path, "rb") as handle:
            response = await self._client.put(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/octet-stream",
                },
                content=handle.read(),
            )
        if response.status_code >= 400:
            raise GraphApiError(response.status_code, response.json())
        return self._to_drive_item(response.json())

    async def delete_item(self, folder_remote_id: str, relative: Path) -> None:
        relative_url = relative.as_posix().lstrip("/")
        url = f"/me/drive/items/{folder_remote_id}:/{relative_url}" if relative_url else f"/me/drive/items/{folder_remote_id}"
        await self._request("DELETE", url)

    def _to_drive_item(self, data: Dict[str, Any]) -> DriveItem:
        return DriveItem(
            id=data["id"],
            name=data.get("name", ""),
            is_folder="folder" in data,
            size=int(data.get("size", 0)),
            parent_reference=data.get("parentReference", {}),
            web_url=data.get("webUrl", ""),
            last_modified=data.get("lastModifiedDateTime", ""),
            e_tag=data.get("eTag"),
        )

