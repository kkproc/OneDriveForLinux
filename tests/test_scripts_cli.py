from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest
from typer.testing import CliRunner

from scripts import cli as cli_module

DEFAULT_DB_PATH = cli_module.DEFAULT_DB_PATH
cli_app = cli_module.app

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))


def test_login_triggers_device_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    client_mock = mock.MagicMock()
    client_mock.acquire_token_silent.return_value = None
    client_mock.acquire_token_device_flow.return_value = {
        "user_code": "ABCD",
        "verification_uri": "https://aka.ms/devicelogin",
    }
    client_mock.poll_device_flow.return_value = {"access_token": "token"}

    monkeypatch.setattr("scripts.cli.create_auth_client", lambda *args, **kwargs: client_mock)

    result = runner.invoke(
        cli_app,
        [
            "login",
            "--client-id",
            "fake-client",
            "--cache-path",
            str(DEFAULT_DB_PATH.with_suffix(".json")),
        ],
        input="fake-client\n",
    )

    assert result.exit_code == 0
    client_mock.acquire_token_device_flow.assert_called_once()
    client_mock.poll_device_flow.assert_called_once()


def test_list_folders_outputs_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    store_mock = mock.MagicMock()
    store_mock.get_folders.return_value = []
    monkeypatch.setattr("scripts.cli.get_config_store", lambda path: store_mock)

    result = runner.invoke(
        cli_app,
        ["folders", "list", "--db-path", "./db.sqlite"],
    )
    assert result.exit_code == 0
    assert "No folders configured" in result.output


def test_add_folder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store_mock = mock.MagicMock()
    monkeypatch.setattr("scripts.cli.get_config_store", lambda path: store_mock)

    local_dir = tmp_path / "Docs"
    result = runner.invoke(
        cli_app,
        [
            "folders",
            "add",
            "remote",
            "--drive-id",
            "drive",
            "--local-path",
            str(local_dir),
        ],
    )
    assert result.exit_code == 0
    store_mock.upsert_folder.assert_called_once()


def test_remove_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    store_mock = mock.MagicMock()
    monkeypatch.setattr("scripts.cli.get_config_store", lambda path: store_mock)

    result = runner.invoke(
        cli_app,
        ["folders", "remove", "remote"],
    )
    assert result.exit_code == 0
    store_mock.remove_folder.assert_called_once_with("remote")


def test_ui_command(monkeypatch: pytest.MonkeyPatch) -> None:
    run_ui_mock = mock.MagicMock()
    monkeypatch.setattr("scripts.cli.run_ui", run_ui_mock)

    result = runner.invoke(cli_app, ["ui"])
    assert result.exit_code == 0
    run_ui_mock.assert_called_once()

