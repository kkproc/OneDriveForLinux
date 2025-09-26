from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.storage.config_store import ConfigStore, FolderConfig, FileState


@pytest.fixture
def store(tmp_path: Path) -> ConfigStore:
    db_path = tmp_path / "sync.db"
    return ConfigStore(db_path)


def test_upsert_and_retrieve_folder(store: ConfigStore, tmp_path: Path) -> None:
    folder = FolderConfig(
        remote_id="remote",
        drive_id="drive",
        display_name="Docs",
        local_path=tmp_path / "Docs",
    )
    store.upsert_folder(folder)

    folders = store.get_folders()
    assert len(folders) == 1
    retrieved = folders[0]
    assert retrieved.remote_id == folder.remote_id
    assert retrieved.local_path == folder.local_path
    assert retrieved.sync_direction == "pull"
    assert retrieved.conflict_policy == "remote_wins"


def test_update_folder_state(store: ConfigStore, tmp_path: Path) -> None:
    folder = FolderConfig(
        remote_id="remote",
        drive_id="drive",
        display_name="Docs",
        local_path=tmp_path / "Docs",
    )
    store.upsert_folder(folder)

    timestamp = datetime.now(timezone.utc)
    store.update_folder_state(
        remote_id="remote",
        delta_link="delta",
        last_synced_at=timestamp,
    )

    updated = store.get_folders()[0]
    assert updated.delta_link == "delta"
    assert updated.last_synced_at == timestamp


def test_update_folder_preferences(store: ConfigStore, tmp_path: Path) -> None:
    folder = FolderConfig(
        remote_id="remote",
        drive_id="drive",
        display_name="Docs",
        local_path=tmp_path / "Docs",
    )
    store.upsert_folder(folder)

    store.update_folder_preferences("remote", sync_direction="bidirectional", conflict_policy="local_wins")
    updated = store.get_folders()[0]
    assert updated.sync_direction == "bidirectional"
    assert updated.conflict_policy == "local_wins"


def test_preferences(store: ConfigStore) -> None:
    assert store.get_preference("sync_frequency") is None
    store.set_preference("sync_frequency", "10")
    assert store.get_preference("sync_frequency") == "10"


def test_file_state_roundtrip(store: ConfigStore, tmp_path: Path) -> None:
    cfg = FolderConfig(
        remote_id="folder",
        drive_id="drive",
        display_name="Docs",
        local_path=tmp_path / "Docs",
    )
    store.upsert_folder(cfg)

    state = FileState(
        folder_remote_id="folder",
        item_id="file1",
        relative_path=Path("Sub/File1.txt"),
        etag="etag1",
        last_modified="2025-09-26T00:00:00Z",
        local_mtime=123.45,
        content_hash="hash",
    )
    store.upsert_file_state(
        state.folder_remote_id,
        state.item_id,
        state.relative_path,
        etag=state.etag,
        last_modified=state.last_modified,
        local_mtime=state.local_mtime,
        content_hash=state.content_hash,
    )

    loaded = store.get_file_state("folder", Path("Sub/File1.txt"))
    assert loaded == state

    store.remove_file_state("folder", Path("Sub/File1.txt"))
    assert store.get_file_state("folder", Path("Sub/File1.txt")) is None

