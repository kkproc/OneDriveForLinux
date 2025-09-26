from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from app.graph.onedrive_client import DriveItem
from app.storage.config_store import ConfigStore, FolderConfig
from app.sync.engine import SyncEngine


@pytest.fixture
def store(tmp_path: Path) -> ConfigStore:
    db_path = tmp_path / "config.db"
    return ConfigStore(db_path)


@pytest.mark.asyncio
async def test_sync_folder_full_download(store: ConfigStore, tmp_path: Path) -> None:
    cfg = FolderConfig(
        remote_id="root",
        drive_id="drive",
        display_name="Docs",
        local_path=tmp_path / "Docs",
    )
    store.upsert_folder(cfg)

    async def token_provider() -> str:
        return "token"

    engine = SyncEngine(token_provider, store)

    client_mock = mock.AsyncMock()
    client_mock.list_children.return_value = _async_iter([[make_drive_item("file1", False)]])
    client_mock.download.return_value = b"content"
    client_mock.delta.return_value = {"@odata.deltaLink": "delta"}
    engine._client = client_mock

    await engine.sync_folder(cfg)

    dest = cfg.local_path / "file1"
    assert dest.exists() and dest.read_bytes() == b"content"
    assert store.get_folders()[0].delta_link == "delta"


@pytest.mark.asyncio
async def test_sync_folder_delta(store: ConfigStore, tmp_path: Path) -> None:
    cfg = FolderConfig(
        remote_id="root",
        drive_id="drive",
        display_name="Docs",
        local_path=tmp_path / "Docs",
        delta_link="deltaLink",
    )
    store.upsert_folder(cfg)

    async def token_provider() -> str:
        return "token"

    engine = SyncEngine(token_provider, store)

    delta_payload = {
        "value": [
            {
                "id": "file1",
                "name": "file1",
                "size": 4,
                "parentReference": {"driveId": "drive", "path": "/drive/root"},
            }
        ],
        "@odata.deltaLink": "delta2",
    }

    delta_mock = mock.AsyncMock()
    delta_mock.delta.return_value = delta_payload
    delta_mock.download.return_value = b"data"
    engine._client = delta_mock

    await engine.sync_folder(cfg)

    assert (cfg.local_path / "file1").read_bytes() == b"data"
    assert store.get_folders()[0].delta_link == "delta2"


@pytest.mark.asyncio
async def test_local_change_detection(store: ConfigStore, tmp_path: Path) -> None:
    cfg = FolderConfig(
        remote_id="root",
        drive_id="drive",
        display_name="Docs",
        local_path=tmp_path / "Docs",
        sync_direction="push",
        conflict_policy="local_wins",
    )
    store.upsert_folder(cfg)

    file_path = cfg.local_path / "file1.txt"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text("hello")

    async def token_provider() -> str:
        return "token"

    engine = SyncEngine(token_provider, store)
    client_mock = mock.AsyncMock()
    uploaded_item = make_drive_item("file1", False)
    client_mock.upload_item = mock.AsyncMock(return_value=uploaded_item)
    client_mock.delete_item = mock.AsyncMock(return_value=None)
    client_mock.delta = mock.AsyncMock(return_value={"value": [], "@odata.deltaLink": "delta"})
    engine._client = client_mock

    await engine.sync_folder(cfg)
    client_mock.upload_item.assert_called_once()


async def _async_iter(sequence):
    for item in sequence:
        yield item


def make_drive_item(name: str, is_folder: bool) -> DriveItem:
    return DriveItem(
        id=name,
        name=name,
        is_folder=is_folder,
        parent_reference={"path": "/drive/root", "driveId": "drive"},
        size=1,
        web_url="",
        last_modified="",
        e_tag=None,
    )
