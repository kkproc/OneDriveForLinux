"""Application bootstrap for OneDrive Linux Sync UI."""

from __future__ import annotations

import asyncio
import base64
import json
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import qasync
from PySide6 import QtWidgets

from app.auth.msal_client import AuthConfig, MSALClient
from app.graph.onedrive_client import DriveItem, OneDriveClient
from app.ui.dialogs import AddAccountDialog
from app.ui.main_window import MainWindow
from app.ui.models import FolderNode
from app.storage.config_store import AccountRecord, ConfigStore, FolderConfig, SyncHistoryRecord
from app.sync.engine import SyncEngine
from app.services.notifier import Notification, Notifier, QtNotifier, connect_qt_notifier

CLIENT_ID = "3f954ce5-c5e0-44b3-95dc-9a05a590a953"
AUTHORITY = "https://login.microsoftonline.com/common"
SCOPES = ["Files.ReadWrite.All", "User.Read"]
CACHE_PATH = Path("~/.cache/onedrive-linux-sync/token_cache.json").expanduser()
DEFAULT_ACCOUNT_ID = "default"


def build_token_provider(account: AccountRecord) -> Callable[[], Awaitable[str]]:
    config = AuthConfig(
        client_id=CLIENT_ID,
        authority=AUTHORITY,
        scopes=SCOPES,
        cache_path=CACHE_PATH,
        keyring_account=account.id,
    )
    client = MSALClient(config)

    async def _provider() -> str:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, client.acquire_token_silent, account.id)
        if not result:
            raise RuntimeError("No cached token; login via CLI first")
        return result["access_token"]

    def _persist_cache() -> None:
        client.persist_cache_for(account.id)

    _provider.persist_cache = _persist_cache  # type: ignore[attr-defined]

    return _provider


def build_device_flow_handler() -> Callable[[], tuple[dict, Callable[[], dict]]]:
    config = AuthConfig(
        client_id=CLIENT_ID,
        authority=AUTHORITY,
        scopes=SCOPES,
        cache_path=CACHE_PATH,
    )
    client = MSALClient(config)

    def _extract_home_account_id(token: dict[str, Any]) -> Optional[str]:
        client_info = token.get("client_info")
        if not client_info:
            return None
        try:
            padding = "=" * (-len(client_info) % 4)
            decoded = base64.urlsafe_b64decode(client_info + padding).decode("utf-8")
            data = json.loads(decoded)
        except (ValueError, json.JSONDecodeError):
            return None
        uid = data.get("uid")
        utid = data.get("utid")
        if uid and utid:
            return f"{uid}.{utid}"
        return None

    def _handler() -> tuple[dict, Callable[[], dict]]:
        baseline_accounts: set[str] = {
            acct_id
            for acct in client.get_accounts()
            if (acct_id := acct.get("home_account_id"))
        }
        flow = client.acquire_token_device_flow()

        def poll() -> dict:
            result = client.poll_device_flow(flow)
            if "access_token" in result:
                accounts_after = {
                    acct_id
                    for acct in client.get_accounts()
                    if (acct_id := acct.get("home_account_id"))
                }
                new_accounts = [acct_id for acct_id in accounts_after if acct_id not in baseline_accounts]
                account_id = new_accounts[0] if new_accounts else _extract_home_account_id(result)
                if not account_id and accounts_after:
                    account_id = next(iter(accounts_after))
                if account_id:
                    result["account_id"] = account_id
                    client.persist_cache_for(account_id)
                    baseline_accounts.update(accounts_after)
            return result

        return flow, poll

    return _handler


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
    except Exception as exc:  # pragma: no cover - surface failure in UI
        node.fetched = False
        window.set_status(f"Failed to load {node.name}: {exc}", 5000)
    finally:
        window.set_loading_complete()

    if not fetched_any:
        node.children.clear()


def run() -> None:
    app = QtWidgets.QApplication(sys.argv)
    store = ConfigStore(Path("~/.local/share/onedrive-linux-sync/config.db").expanduser())
    accounts = store.get_accounts()
    if not accounts:
        default_account = AccountRecord(
            id=DEFAULT_ACCOUNT_ID,
            username="default",
            display_name="Default Account",
            account_type="personal",
        )
        store.upsert_account(default_account)
        accounts = [default_account]

    active_account = accounts[0]
    token_provider = build_token_provider(active_account)
    od_client = OneDriveClient(token_provider)

    device_flow_handler = build_device_flow_handler()
    window = MainWindow(store, active_account, token_provider, device_flow_handler=device_flow_handler)
    window.show()
    window._progress.show()

    notifier = Notifier()
    qt_notifier = QtNotifier()
    connect_qt_notifier(notifier, qt_notifier)
    qt_notifier.notification.connect(lambda event: window.set_status(event.message, 5000))

    async def notification_callback(event: Notification) -> None:
        notifier.dispatch(event)

    engine = SyncEngine(token_provider, store, account_id=active_account.id, notifier=notification_callback)
    engine.parent_widget = window

    existing = store.get_folders(account_id=active_account.id)
    window.set_selected_nodes(existing)
    window.set_accounts(accounts)

    def rebuild_token_provider(account: AccountRecord) -> Callable[[], Awaitable[str]]:
        return build_token_provider(account)

    def update_engine_account(account: AccountRecord) -> None:
        nonlocal token_provider, od_client
        engine._account_id = account.id
        token_provider = rebuild_token_provider(account)
        engine._token_provider = token_provider
        old_client = od_client
        od_client = OneDriveClient(token_provider)
        if old_client is not None:
            loop.create_task(old_client.close())
        window.apply_active_account(account)
        window.set_selected_nodes(store.get_folders(account.id))
        token_provider.persist_cache()

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    app.aboutToQuit.connect(loop.stop)

    async def populate_root() -> None:
        window.set_loading_complete()
        window._progress.show()
        try:
            root_item = await od_client.get_drive_root()
        except Exception as exc:  # pragma: no cover - display error in UI
            window._progress.hide()
            window.set_status(f"Failed to load OneDrive data: {exc}", 5000)
            return
        node = FolderNode(
            id=root_item.id,
            name=root_item.name or "OneDrive",
            drive_id=root_item.parent_reference.get("driveId") if root_item.parent_reference else None,
            is_folder=True,
            fetched=False,
        )
        window.populate_root([node])
        await load_children(od_client, node, window)

    def schedule(child_node: FolderNode) -> None:
        loop.create_task(load_children(od_client, child_node, window))

    def handle_selection(node: FolderNode, selected: bool, path: str, direction: str, conflict: str) -> None:
        if selected:
            store.upsert_folder(
                FolderConfig(
                    account_id=window.active_account.id,
                    remote_id=node.id,
                    drive_id=node.drive_id or "default",
                    display_name=node.name,
                    local_path=Path(path),
                    sync_direction=direction,
                    conflict_policy=conflict,
                )
            )
            cfg = store.get_folder(window.active_account.id, node.id)
            if cfg:
                window.history_requested.emit(cfg.remote_id, cfg.display_name)
        else:
            store.remove_folder(window.active_account.id, node.id)
            window.history_requested.emit(node.id, node.name)

    async def run_sync(node: FolderNode) -> None:
        cfg = next(
            (
                c
                for c in store.get_folders(account_id=window.active_account.id)
                if c.remote_id == node.id
            ),
            None,
        )
        if not cfg:
            return
        await engine.sync_folder(cfg)
        window.set_status(f"Synced {node.name}")
        history = store.get_recent_history(cfg.account_id, cfg.remote_id)
        window.update_history(cfg.display_name, history)

    def trigger_sync(node: FolderNode) -> None:
        loop.create_task(run_sync(node))

    def update_history(remote_id: str, display_name: str) -> None:
        history = store.get_recent_history(window.active_account.id, remote_id)
        window.update_history(display_name, history)

    def handle_account_created(token: dict) -> None:
        account_info = token.get("id_token_claims") or {}
        account_id = token.get("account_id") or account_info.get("home_account_id")
        username = account_info.get("preferred_username") or account_info.get("unique_name") or account_id
        display_name = account_info.get("name") or username
        tenant_id = account_info.get("tid")
        if not account_id:
            QtWidgets.QMessageBox.warning(window, "Account Error", "Unable to determine account id from token.")
            return
        config = AuthConfig(
            client_id=CLIENT_ID,
            authority=AUTHORITY,
            scopes=SCOPES,
            cache_path=CACHE_PATH,
        )
        client = MSALClient(config)
        client.persist_cache_for(account_id)
        new_account = AccountRecord(
            id=account_id,
            username=username,
            display_name=display_name,
            tenant_id=tenant_id,
            account_type="business" if tenant_id and tenant_id != tenant_id.lower() else "personal",
        )
        store.upsert_account(new_account)
        window.set_accounts(store.get_accounts())
        update_engine_account(new_account)
        window.history_requested.emit(new_account.id, new_account.display_name)
        loop.create_task(populate_root())

    def handle_account_switch(account_id: str) -> None:
        account = store.get_account(account_id)
        if not account:
            return
        update_engine_account(account)
        window.history_requested.emit(account.id, account.display_name)
        loop.create_task(populate_root())

    def handle_account_remove(account_id: str) -> None:
        store.remove_account(account_id, cascade=True)
        accounts = store.get_accounts()
        if not accounts:
            default_account = AccountRecord(
                id=DEFAULT_ACCOUNT_ID,
                username="default",
                display_name="Default Account",
                account_type="personal",
            )
            store.upsert_account(default_account)
            accounts = [default_account]
        new_active = accounts[0]
        window.set_accounts(accounts)
        update_engine_account(new_active)
        window.history_requested.emit(new_active.id, new_active.display_name)
        loop.create_task(populate_root())

    window.load_children_requested.connect(schedule)
    window.selection_toggled.connect(handle_selection)
    window.sync_requested.connect(trigger_sync)
    window.history_requested.connect(update_history)
    window.account_created.connect(handle_account_created)
    window.account_switch_requested.connect(handle_account_switch)
    window.account_remove_requested.connect(handle_account_remove)

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
