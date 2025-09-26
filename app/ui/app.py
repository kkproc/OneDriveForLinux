"""Application bootstrap for OneDrive Linux Sync UI."""

from __future__ import annotations

import asyncio
import sys
from functools import partial
from pathlib import Path
from typing import Awaitable, Callable

import qasync
from PySide6 import QtWidgets

from app.auth.msal_client import AuthConfig, MSALClient
from app.graph.onedrive_client import DriveItem, OneDriveClient
from app.ui.main_window import MainWindow
from app.ui.models import FolderNode
from app.storage.config_store import ConfigStore, FolderConfig
from app.sync.engine import SyncEngine

CLIENT_ID = "3f954ce5-c5e0-44b3-95dc-9a05a590a953"
AUTHORITY = "https://login.microsoftonline.com/common"
SCOPES = ["Files.ReadWrite.All", "User.Read"]
CACHE_PATH = Path("~/.cache/onedrive-linux-sync/token_cache.json").expanduser()


def build_token_provider() -> Callable[[], Awaitable[str]]:
    config = AuthConfig(
        client_id=CLIENT_ID,
        authority=AUTHORITY,
        scopes=SCOPES,
        cache_path=CACHE_PATH,
    )
    client = MSALClient(config)

    async def _provider() -> str:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, client.acquire_token_silent)
        if not result:
            raise RuntimeError("No cached token; login via CLI first")
        return result["access_token"]

    return _provider


async def load_children(od_client: OneDriveClient, node: FolderNode, window: MainWindow) -> None:
    if node.fetched:
        return
    node.fetched = True
    drive_id = node.drive_id
    if drive_id is None:
        root = await od_client.get_drive_root()
        drive_id = root.parent_reference.get("driveId") if root.parent_reference else None
        node.drive_id = drive_id

    async def mapper(item: DriveItem) -> FolderNode:
        child_drive_id = drive_id
        if item.parent_reference and item.parent_reference.get("driveId"):
            child_drive_id = item.parent_reference["driveId"]
        return FolderNode(
            id=item.id,
            name=item.name or "(unnamed)",
            drive_id=child_drive_id,
            is_folder=item.is_folder,
        )

    fetched_any = False
    try:
        async for page in od_client.list_children(node.id, drive_id=drive_id):
            if not page:
                continue
            fetched_any = True
            children = [await mapper(item) for item in page]
            window.append_children(node, children)
    finally:
        window.set_loading_complete()

    if not fetched_any:
        node.children.clear()


def run() -> None:
    app = QtWidgets.QApplication(sys.argv)
    token_provider = build_token_provider()
    od_client = OneDriveClient(token_provider)
    store = ConfigStore(Path("~/.local/share/onedrive-linux-sync/config.db").expanduser())

    window = MainWindow(store, token_provider)
    window.show()
    window._progress.show()

    engine = SyncEngine(token_provider, store)

    existing = store.get_folders()
    window.set_selected_nodes(existing)

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    app.aboutToQuit.connect(loop.stop)

    async def populate_root() -> None:
        window.set_loading_complete()
        window._progress.show()
        root_item = await od_client.get_drive_root()
        node = FolderNode(
            id=root_item.id,
            name=root_item.name or "OneDrive",
            drive_id=root_item.parent_reference.get("driveId") if root_item.parent_reference else None,
            is_folder=True,
            fetched=False,
        )
        window.populate_root([node])

        def schedule(child_node: FolderNode) -> None:
            loop.create_task(load_children(od_client, child_node, window))

        def handle_selection(node: FolderNode, selected: bool, path: str, direction: str, conflict: str) -> None:
            if selected:
                store.upsert_folder(
                    FolderConfig(
                        remote_id=node.id,
                        drive_id=node.drive_id or "default",
                        display_name=node.name,
                        local_path=Path(path),
                        sync_direction=direction,
                        conflict_policy=conflict,
                    )
                )
            else:
                store.remove_folder(node.id)

        async def run_sync(node: FolderNode) -> None:
            cfg = next((c for c in store.get_folders() if c.remote_id == node.id), None)
            if not cfg:
                return
            await engine.sync_folder(cfg)
            window.set_status(f"Synced {node.name}")

        def trigger_sync(node: FolderNode) -> None:
            loop.create_task(run_sync(node))

        window.load_children_requested.connect(schedule)
        window.selection_toggled.connect(handle_selection)
        window.sync_requested.connect(trigger_sync)
        await load_children(od_client, node, window)

    with loop:
        loop.create_task(populate_root())
        try:
            loop.run_forever()
        except KeyboardInterrupt:
            pass
        finally:
            loop.run_until_complete(od_client.close())
            loop.run_until_complete(engine.close())


if __name__ == "__main__":  # pragma: no cover
    run()
