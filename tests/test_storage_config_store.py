from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.storage.config_store import ConfigStore, FolderConfig, FileState, AccountRecord


@pytest.fixture
def store(tmp_path: Path) -> ConfigStore:
    db_path = tmp_path / "sync.db"
    return ConfigStore(db_path)


def test_upsert_and_retrieve_folder(store: ConfigStore, tmp_path: Path) -> None:
    account = AccountRecord(id="acct", username="user@example.com", display_name="User")
    store.upsert_account(account)
    folder = FolderConfig(
        account_id=account.id,
        remote_id="remote",
        drive_id="drive",
        display_name="Docs",
        local_path=tmp_path / "Docs",
    )
    store.upsert_folder(folder)

    folders = store.get_folders()
    assert len(folders) == 1
    retrieved = folders[0]
    assert retrieved.account_id == account.id
    assert retrieved.remote_id == folder.remote_id
    assert retrieved.local_path == folder.local_path
    assert retrieved.sync_direction == "pull"
    assert retrieved.conflict_policy == "remote_wins"


def test_update_folder_state(store: ConfigStore, tmp_path: Path) -> None:
    account = AccountRecord(id="acct", username="user@example.com", display_name="User")
    store.upsert_account(account)
    folder = FolderConfig(
        account_id=account.id,
        remote_id="remote",
        drive_id="drive",
        display_name="Docs",
        local_path=tmp_path / "Docs",
    )
    store.upsert_folder(folder)

    timestamp = datetime.now(timezone.utc)
    store.update_folder_state(
        account_id=account.id,
        remote_id="remote",
        delta_link="delta",
        last_synced_at=timestamp,
        last_status="success",
        last_error=None,
    )

    updated = store.get_folders(account_id=account.id)[0]
    assert updated.delta_link == "delta"
    assert updated.last_synced_at == timestamp
    assert updated.last_status == "success"
    assert updated.last_error is None


def test_update_folder_preferences(store: ConfigStore, tmp_path: Path) -> None:
    account = AccountRecord(id="acct", username="user@example.com", display_name="User")
    store.upsert_account(account)
    folder = FolderConfig(
        account_id=account.id,
        remote_id="remote",
        drive_id="drive",
        display_name="Docs",
        local_path=tmp_path / "Docs",
    )
    store.upsert_folder(folder)

    store.update_folder_preferences(account.id, "remote", sync_direction="bidirectional", conflict_policy="local_wins")
    updated = store.get_folders(account_id=account.id)[0]
    assert updated.sync_direction == "bidirectional"
    assert updated.conflict_policy == "local_wins"


def test_preferences(store: ConfigStore) -> None:
    assert store.get_preference("sync_frequency") is None
    store.set_preference("sync_frequency", "10")
    assert store.get_preference("sync_frequency") == "10"

    account = AccountRecord(id="acct", username="user@example.com", display_name="User")
    store.upsert_account(account)
    assert store.get_preference("sync_frequency", account_id=account.id) is None
    store.set_preference("sync_frequency", "15", account_id=account.id)
    assert store.get_preference("sync_frequency", account_id=account.id) == "15"


def test_file_state_roundtrip(store: ConfigStore, tmp_path: Path) -> None:
    account = AccountRecord(id="acct", username="user@example.com", display_name="User")
    store.upsert_account(account)
    cfg = FolderConfig(
        account_id=account.id,
        remote_id="folder",
        drive_id="drive",
        display_name="Docs",
        local_path=tmp_path / "Docs",
    )
    store.upsert_folder(cfg)

    state = FileState(
        account_id=account.id,
        folder_remote_id="folder",
        item_id="file1",
        relative_path=Path("Sub/File1.txt"),
        etag="etag1",
        last_modified="2025-09-26T00:00:00Z",
        local_mtime=123.45,
        content_hash="hash",
    )
    store.upsert_file_state(
        account.id,
        state.folder_remote_id,
        state.item_id,
        state.relative_path,
        etag=state.etag,
        last_modified=state.last_modified,
        local_mtime=state.local_mtime,
        content_hash=state.content_hash,
    )

    loaded = store.get_file_state(account.id, "folder", Path("Sub/File1.txt"))
    assert loaded == state

    store.remove_file_state(account.id, "folder", Path("Sub/File1.txt"))
    assert store.get_file_state(account.id, "folder", Path("Sub/File1.txt")) is None


def test_get_latest_account_history(store: ConfigStore) -> None:
    account = AccountRecord(id="acct", username="user@example.com", display_name="User")
    store.upsert_account(account)

    earlier = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    later = datetime(2024, 1, 2, 13, 30, tzinfo=timezone.utc)
    store.record_sync_event(account.id, "folder-a", "success", finished_at=earlier)
    store.record_sync_event(account.id, "folder-b", "error", finished_at=later, error_message="boom")

    latest = store.get_latest_account_history(account.id)
    assert latest is not None
    assert latest.status == "error"
    assert latest.folder_remote_id == "folder-b"
    assert latest.error_message == "boom"
