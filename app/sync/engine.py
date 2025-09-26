"""Sync engine handling pull synchronization from OneDrive to local folders."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Iterable, Optional

from app.graph.onedrive_client import DriveItem, OneDriveClient
from app.storage.config_store import ConfigStore, FolderConfig

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SyncContext:
    config: FolderConfig
    local_root: Path
    delta_link: Optional[str]


class SyncEngine:
    def __init__(
        self,
        token_provider: Callable[[], Awaitable[str]],
        store: ConfigStore,
        download_chunk_size: int = 8,
    ) -> None:
        self._token_provider = token_provider
        self._store = store
        self._download_chunk_size = download_chunk_size
        self._client: Optional[OneDriveClient] = None

    async def _ensure_client(self) -> OneDriveClient:
        if self._client is None:
            self._client = OneDriveClient(self._token_provider)
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.close()
            self._client = None

    async def sync_all(self) -> None:
        folders = self._store.get_folders()
        if not folders:
            logger.info("No folders selected for sync")
            return

        for cfg in folders:
            await self.sync_folder(cfg)

    async def sync_folder(self, cfg: FolderConfig) -> None:
        logger.info("Syncing folder %s", cfg.display_name)
        client = await self._ensure_client()
        local_root = cfg.local_path
        local_root.mkdir(parents=True, exist_ok=True)

        ctx = SyncContext(config=cfg, local_root=local_root, delta_link=cfg.delta_link)
        if ctx.delta_link:
            await self._process_delta(client, ctx)
        else:
            await self._full_sync(client, ctx)

    def _record_file_state(self, ctx: SyncContext, item: DriveItem, dest: Path) -> None:
        etag = item.e_tag
        last_modified = item.last_modified
        mtime = dest.stat().st_mtime if dest.exists() else None
        relative = dest.relative_to(ctx.local_root)
        self._store.upsert_file_state(
            ctx.config.remote_id,
            item.id,
            relative,
            etag=etag,
            last_modified=last_modified,
            local_mtime=mtime,
        )

    async def _iter_children(self, client: OneDriveClient, remote_id: str, drive_id: Optional[str]):
        result = client.list_children(remote_id, drive_id=drive_id)
        if hasattr(result, "__aiter__"):
            async for page in result:
                yield page
        else:
            resolved = await result
            if hasattr(resolved, "__aiter__"):
                async for page in resolved:
                    yield page
            else:
                for page in resolved:
                    yield page

    async def _full_sync(self, client: OneDriveClient, ctx: SyncContext) -> None:
        queue = [(ctx.config.remote_id, ctx.config.drive_id)]
        while queue:
            remote_id, drive_id = queue.pop()
            async for page in self._iter_children(client, remote_id, drive_id):
                for item in page:
                    dest = self._destination_path(ctx.local_root, item)
                    if item.is_folder:
                        dest.mkdir(parents=True, exist_ok=True)
                        next_drive = item.parent_reference.get("driveId") if item.parent_reference else drive_id
                        queue.append((item.id, next_drive))
                    else:
                        await self._download_item(client, item, dest)
                        self._record_file_state(ctx, item, dest)
        delta = await client.delta(ctx.config.remote_id)
        self._store.update_folder_state(ctx.config.remote_id, delta_link=delta.get("@odata.deltaLink"))

    async def _process_delta(self, client: OneDriveClient, ctx: SyncContext) -> None:
        delta_link = ctx.delta_link
        last_delta = delta_link
        while delta_link:
            payload = await client.delta(ctx.config.remote_id, delta_link=delta_link)
            next_link = payload.get("@odata.nextLink")
            last_delta = payload.get("@odata.deltaLink") or last_delta
            delta_link = next_link
            for item in payload.get("value", []):
                if "deleted" in item:
                    drive_item = self._drive_item_from_dict(item)
                    dest = self._destination_path(ctx.local_root, drive_item)
                    self._handle_delete(dest)
                else:
                    drive_item = self._drive_item_from_dict(item)
                    dest = self._destination_path(ctx.local_root, drive_item)
                    if drive_item.is_folder:
                        dest.mkdir(parents=True, exist_ok=True)
                    else:
                        await self._download_item(client, drive_item, dest)
                        self._record_file_state(ctx, drive_item, dest)
        if last_delta:
            self._store.update_folder_state(ctx.config.remote_id, delta_link=last_delta)

    def _drive_item_from_dict(self, data: dict) -> DriveItem:
        parent_ref = data.get("parentReference", {}) or {}
        return DriveItem(
            id=data.get("id", ""),
            name=data.get("name", ""),
            is_folder="folder" in data,
            size=int(data.get("size", 0) or 0),
            parent_reference=parent_ref,
            web_url=data.get("webUrl", ""),
            last_modified=data.get("lastModifiedDateTime", ""),
            e_tag=data.get("eTag"),
        )

    def _destination_path(self, root: Path, item: DriveItem) -> Path:
        parents = item.parent_reference.get("path", "") if item.parent_reference else ""
        relative = Path(item.name)
        if parents:
            parts = [p for p in parents.split("/") if p and p not in ("drive", "root")]
            relative = Path(*parts, item.name) if parts else Path(item.name)
        return root / relative

    def _handle_delete(self, dest: Path) -> None:
        if dest.exists():
            if dest.is_dir():
                for child in dest.iterdir():
                    if child.is_dir():
                        self._handle_delete(child)
                    else:
                        child.unlink()
                dest.rmdir()
            else:
                dest.unlink()

    async def _download_item(self, client: OneDriveClient, item: DriveItem, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        drive_id = item.parent_reference.get("driveId") if item.parent_reference else None
        content = await client.download(item.id, drive_id=drive_id)
        temp = dest.with_suffix(dest.suffix + ".tmp")
        with open(temp, "wb") as handle:
            handle.write(content)
        temp.replace(dest)
        logger.debug("Downloaded %s", dest)
