"""Sync engine handling synchronization between OneDrive and local folders."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Dict, Iterable, List, Optional

from PySide6 import QtWidgets

from app.graph.onedrive_client import DriveItem, OneDriveClient
from app.services.notifier import Notification
from app.storage.config_store import ConfigStore, FileState, FolderConfig

from app.logging_utils import setup_logging

logger = logging.getLogger(__name__)
setup_logging()


@dataclass(slots=True)
class SyncContext:
    config: FolderConfig
    local_root: Path
    delta_link: Optional[str]


@dataclass(slots=True)
class LocalChange:
    relative_path: Path
    absolute_path: Path
    state: Optional[FileState]
    content_hash: Optional[str]


@dataclass(slots=True)
class RemoteChange:
    item: DriveItem
    deleted: bool = False


class LocalWalker:
    def __init__(self, root: Path) -> None:
        self.root = root

    def iter_files(self) -> Iterable[Path]:
        if not self.root.exists():
            return []
        return (path for path in self.root.glob("**/*") if path.is_file())

    @staticmethod
    def hash_file(path: Path) -> Optional[str]:
        hasher = hashlib.blake2b(digest_size=16)
        try:
            with open(path, "rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    hasher.update(chunk)
            return hasher.hexdigest()
        except FileNotFoundError:
            return None


class SyncEngine:
    def __init__(
        self,
        token_provider: Callable[[], Awaitable[str]],
        store: ConfigStore,
        *,
        notifier: Optional[Callable[[Notification], Awaitable[None]]] = None,
        download_chunk_size: int = 8,
    ) -> None:
        self._token_provider = token_provider
        self._store = store
        self._download_chunk_size = max(1, download_chunk_size)
        self._client: Optional[OneDriveClient] = None
        self.parent_widget: Optional[QtWidgets.QWidget] = None
        self._notifier = notifier

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
        local_root = self._compute_local_root(cfg)
        logger.debug("Computed local root for %s: %s", cfg.remote_id, local_root)
        local_root.mkdir(parents=True, exist_ok=True)
        logger.debug("Root exists after mkdir: %s", local_root.exists())
        logger.debug("Immediate root contents: %s", list(local_root.iterdir()) if local_root.exists() else [])
        ctx = SyncContext(
            config=cfg,
            local_root=local_root,
            delta_link=cfg.delta_link,
        )
        local_changes = self._detect_local_changes(ctx)
        logger.debug("Detected %s local changes for %s", len(local_changes), cfg.remote_id)
        if ctx.delta_link:
            remote_changes = await self._collect_remote_changes(client, ctx)
        else:
            remote_changes = await self._collect_full_listing(client, ctx)
        logger.debug("Collected %s remote changes for %s", len(remote_changes), cfg.remote_id)

        status = "success"
        error_message: Optional[str] = None
        start_time = datetime.now(timezone.utc)

        try:
            await self._reconcile(client, ctx, local_changes, remote_changes)
        except Exception as exc:  # noqa: BLE001
            status = "error"
            error_message = str(exc)
            logger.exception("Sync failed for %s", cfg.display_name)
            raise
        else:
            logger.info("Sync complete for %s", cfg.display_name)
        finally:
            finished_at = datetime.now(timezone.utc)
            if ctx.delta_link:
                self._store.update_folder_state(
                    cfg.remote_id,
                    delta_link=ctx.delta_link,
                    last_synced_at=finished_at,
                    last_status=status,
                    last_error=error_message,
                )
            else:
                self._store.update_folder_state(
                    cfg.remote_id,
                    last_synced_at=finished_at,
                    last_status=status,
                    last_error=error_message,
                )
            self._store.record_sync_event(
                cfg.remote_id,
                status,
                finished_at=finished_at,
                error_message=error_message,
            )
            if self._notifier:
                try:
                    await self._notifier(
                        Notification(
                            title=f"Sync {status}",
                            message=f"{cfg.display_name}: {status}",
                            urgency="critical" if status == "error" else "normal",
                        )
                    )
                except Exception:  # pragma: no cover
                    logger.exception("Failed to dispatch notification")

    def _detect_local_changes(self, ctx: SyncContext) -> Dict[str, LocalChange]:
        changes: Dict[str, LocalChange] = {}
        known_states = {state.relative_path.as_posix(): state for state in self._store.iter_file_states(ctx.config.remote_id)}

        walker = LocalWalker(ctx.local_root)
        logger.debug("Scanning local root %s for changes", ctx.local_root)
        for path in walker.iter_files():
            logger.debug("Inspecting local path: %s", path)
            relative = path.relative_to(ctx.local_root)
            key = relative.as_posix()
            state = known_states.pop(key, None)
            mtime = path.stat().st_mtime
            current_hash = walker.hash_file(path)
            if (
                not state
                or (state.local_mtime and abs(state.local_mtime - mtime) > 1e-3)
                or state.content_hash != current_hash
            ):
                logger.debug(
                    "Detected change - relative: %s, previous_state: %s, new_hash: %s",
                    relative,
                    state,
                    current_hash,
                )
                changes[key] = LocalChange(
                    relative_path=relative,
                    absolute_path=path,
                    state=state,
                    content_hash=current_hash,
                )

        for key, state in known_states.items():
            logger.debug("Detected deletion candidate: %s", key)
            changes[key] = LocalChange(
                relative_path=Path(key),
                absolute_path=ctx.local_root / key,
                state=state,
                content_hash=None,
            )

        return changes

    async def _collect_remote_changes(self, client: OneDriveClient, ctx: SyncContext) -> List[RemoteChange]:
        delta_link = ctx.delta_link
        collected: List[RemoteChange] = []
        last_delta = delta_link
        while delta_link:
            payload = self._normalize_delta(await client.delta(ctx.config.remote_id, delta_link=delta_link))
            delta_link = payload.get("@odata.nextLink")
            last_delta = payload.get("@odata.deltaLink") or last_delta
            for item in payload.get("value", []):
                deleted = item.get("deleted") is not None
                drive_item = self._drive_item_from_dict(item)
                collected.append(RemoteChange(item=drive_item, deleted=deleted))
        if last_delta:
            ctx.delta_link = last_delta
            self._store.update_folder_state(ctx.config.remote_id, delta_link=last_delta)
        return collected

    async def _collect_full_listing(self, client: OneDriveClient, ctx: SyncContext) -> List[RemoteChange]:
        collected: List[RemoteChange] = []
        queue = [(ctx.config.remote_id, ctx.config.drive_id, Path())]
        while queue:
            remote_id, drive_id, prefix = queue.pop()
            async for page in self._iter_children(client, remote_id, drive_id):
                for item in page:
                    collected.append(RemoteChange(item=item, deleted=False))
                    if item.is_folder:
                        next_drive = item.parent_reference.get("driveId") if item.parent_reference else drive_id
                        queue.append((item.id, next_drive, prefix / item.name))
                    else:
                        dest = self._destination_path(ctx, item)
                        await self._download_item(client, item, dest)
                        self._record_file_state(ctx, item, dest)
        delta = self._normalize_delta(await client.delta(ctx.config.remote_id))
        ctx.delta_link = delta.get("@odata.deltaLink")
        return collected

    async def _reconcile(
        self,
        client: OneDriveClient,
        ctx: SyncContext,
        local_changes: Dict[str, LocalChange],
        remote_changes: List[RemoteChange],
    ) -> None:
        direction = ctx.config.sync_direction or "pull"
        conflict_policy = ctx.config.conflict_policy or "remote_wins"
        remote_map = {
            self._destination_path(ctx, change.item).relative_to(ctx.local_root).as_posix(): change
            for change in remote_changes
        }

        for key, remote_change in remote_map.items():
            local_change = local_changes.pop(key, None)
            item = remote_change.item
            dest = self._destination_path(ctx, item)
            if local_change:
                await self._resolve_conflict(client, ctx, local_change, remote_change, dest, direction, conflict_policy)
            else:
                if direction in ("pull", "bidirectional"):
                    if remote_change.deleted:
                        self._handle_delete(dest)
                        self._store.remove_file_state(ctx.config.remote_id, dest.relative_to(ctx.local_root))
                    elif item.is_folder:
                        dest.mkdir(parents=True, exist_ok=True)
                    else:
                        await self._download_item(client, item, dest)
                        self._record_file_state(ctx, item, dest)

        for key, local_change in local_changes.items():  # remaining local operations
            path = local_change.absolute_path
            relative = local_change.relative_path
            if local_change.state and not path.exists():
                if direction in ("push", "bidirectional"):
                    await self._delete_remote(client, ctx, local_change)
                self._store.remove_file_state(ctx.config.remote_id, relative)
            elif path.exists() and direction in ("push", "bidirectional"):
                await self._upload_item(client, ctx, path, relative)

    async def _resolve_conflict(
        self,
        client: OneDriveClient,
        ctx: SyncContext,
        local_change: LocalChange,
        remote_change: RemoteChange,
        dest: Path,
        direction: str,
        conflict_policy: str,
    ) -> None:
        item = remote_change.item
        local_exists = local_change.absolute_path.exists()
        remote_deleted = remote_change.deleted

        if conflict_policy == "remote_wins" or direction == "pull":
            if remote_deleted:
                self._handle_delete(dest)
                self._store.remove_file_state(ctx.config.remote_id, local_change.relative_path)
            elif item.is_folder:
                dest.mkdir(parents=True, exist_ok=True)
            else:
                await self._download_item(client, item, dest)
                self._record_file_state(ctx, item, dest)
        elif conflict_policy == "local_wins" or direction == "push":
            if local_exists:
                await self._upload_item(client, ctx, local_change.absolute_path, local_change.relative_path)
            else:
                await self._delete_remote(client, ctx, local_change)
        else:
            choice = self._prompt_conflict(local_change.relative_path)
            if choice == "remote":
                await self._download_item(client, item, dest)
                self._record_file_state(ctx, item, dest)
            elif choice == "local":
                if local_exists:
                    await self._upload_item(client, ctx, local_change.absolute_path, local_change.relative_path)
                else:
                    await self._delete_remote(client, ctx, local_change)
            else:
                logger.info("Conflict skipped for %s", local_change.relative_path)

    def _prompt_conflict(self, relative: Path) -> str:
        app = QtWidgets.QApplication.instance()
        if self.parent_widget is None and not app:
            return "remote"
        if self.parent_widget is None:
            return "remote"
        box = QtWidgets.QMessageBox(self.parent_widget)
        box.setWindowTitle("Sync Conflict")
        box.setText(f"Conflict detected for {relative}.")
        remote_button = box.addButton("Use Remote", QtWidgets.QMessageBox.AcceptRole)
        local_button = box.addButton("Use Local", QtWidgets.QMessageBox.AcceptRole)
        box.addButton(QtWidgets.QMessageBox.Cancel)
        box.exec()
        if box.clickedButton() == local_button:
            return "local"
        if box.clickedButton() == remote_button:
            return "remote"
        return "skip"

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
                    dest = self._destination_path(ctx, item)
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
                    dest = self._destination_path(ctx, drive_item)
                    self._handle_delete(dest)
                else:
                    drive_item = self._drive_item_from_dict(item)
                    dest = self._destination_path(ctx, drive_item)
                    if drive_item.is_folder:
                        dest.mkdir(parents=True, exist_ok=True)
                    else:
                        await self._download_item(client, drive_item, dest)
                        self._record_file_state(ctx, drive_item, dest)
        if last_delta:
            self._store.update_folder_state(ctx.config.remote_id, delta_link=last_delta)

    def _normalize_delta(self, result) -> Dict:
        if isinstance(result, dict):
            return result
        return {}

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

    def _destination_path(self, ctx: SyncContext, item: DriveItem) -> Path:
        root = ctx.local_root
        parent_reference = item.parent_reference or {}
        parent_path = parent_reference.get("path") or parent_reference.get("parentReference", {}).get("path")
        relative_parts: List[str]

        if parent_path:
            if "root:" in parent_path:
                suffix = parent_path.split("root:", 1)[1]
                relative_parts = [p for p in suffix.split("/") if p]
            else:
                relative_parts = [p for p in parent_path.split("/") if p]
            if relative_parts and relative_parts[0] in {"drive", "drives"}:
                relative_parts = relative_parts[2:]
            if relative_parts and relative_parts[0] == ctx.config.display_name:
                relative_parts = relative_parts[1:]
        else:
            relative_parts = []

        local_dir = root.joinpath(*relative_parts)
        local_dir.mkdir(parents=True, exist_ok=True)
        return local_dir / item.name

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

    async def _upload_item(self, client: OneDriveClient, ctx: SyncContext, path: Path, relative: Path) -> None:
        logger.debug("Uploading %s", path)
        remote_relative = self._remote_relative_path(ctx, relative)
        logger.debug(
            "Uploading file - absolute: %s, relative: %s, remote_relative: %s",
            path,
            relative,
            remote_relative,
        )
        result = await client.upload_item(ctx.config.remote_id, path, remote_relative, drive_id=ctx.config.drive_id)
        content_hash = LocalWalker.hash_file(path)
        self._store.upsert_file_state(
            ctx.config.remote_id,
            result.id,
            relative,
            etag=result.e_tag,
            last_modified=result.last_modified,
            local_mtime=path.stat().st_mtime,
            content_hash=content_hash,
        )

    async def _delete_remote(self, client: OneDriveClient, ctx: SyncContext, change: LocalChange) -> None:
        logger.debug("Deleting remote item %s", change.relative_path)
        remote_relative = self._remote_relative_path(ctx, change.relative_path)
        logger.debug(
            "Deleting remote file - relative: %s, remote_relative: %s",
            change.relative_path,
            remote_relative,
        )
        await client.delete_item(ctx.config.remote_id, remote_relative, drive_id=ctx.config.drive_id)
        self._store.remove_file_state(ctx.config.remote_id, change.relative_path)

    def _record_file_state(self, ctx: SyncContext, item: DriveItem, dest: Path) -> None:
        etag = item.e_tag
        last_modified = item.last_modified
        mtime = dest.stat().st_mtime if dest.exists() else None
        relative = dest.relative_to(ctx.local_root)
        content_hash = LocalWalker.hash_file(dest) if dest.exists() else None
        self._store.upsert_file_state(
            ctx.config.remote_id,
            item.id,
            relative,
            etag=etag,
            last_modified=last_modified,
            local_mtime=mtime,
            content_hash=content_hash,
        )

    def _remote_relative_path(self, ctx: SyncContext, relative: Path) -> Path:
        parts = [p for p in relative.parts if p]
        drive_id = (ctx.config.drive_id or "").strip()
        if drive_id and parts[:2] == ["drives", drive_id]:
            parts = parts[2:]
        if parts and parts[0] in {"root", "root:"}:
            parts = parts[1:]
        display = (ctx.config.display_name or "").strip()
        if parts and display and self._normalize_part(parts[0]) == self._normalize_part(display):
            parts = parts[1:]
        return Path(*parts)

    async def run_headless(self) -> None:
        await self.sync_all()
        await self.close()

    def _compute_local_root(self, cfg: FolderConfig) -> Path:
        base = cfg.local_path
        parts = [self._normalize_part(p) for p in base.parts]
        if "drives" in parts or "root:" in parts:
            return base

        display = self._normalize_part((cfg.display_name or "").strip())
        if parts and display and parts[-1] == display:
            return base

        segments: list[str] = []
        drive_id = (cfg.drive_id or "").strip()
        if drive_id:
            segments.extend(["drives", drive_id])
        segments.append("root:")
        if display:
            segments.append(cfg.display_name)

        if not segments:
            return base

        return base.joinpath(*segments)

    @staticmethod
    def _path_endswith(path: Path, suffix: list[str]) -> bool:
        if not suffix:
            return True
        parts = list(path.parts)
        if len(parts) < len(suffix):
            return False
        normalized_parts = [SyncEngine._normalize_part(p) for p in parts[-len(suffix):]]
        normalized_suffix = [SyncEngine._normalize_part(p) for p in suffix]
        return normalized_parts == normalized_suffix

    @staticmethod
    def _normalize_part(part: str) -> str:
        if part in {"root", "root:"}:
            return "root:"
        return part
