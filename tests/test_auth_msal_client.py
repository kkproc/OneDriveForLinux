from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from app.auth.msal_client import AuthConfig, MSALClient


@pytest.fixture
def temp_cache_path(tmp_path: Path) -> Path:
    return tmp_path / "token_cache.json"


def test_silent_token_return_none_when_no_account(temp_cache_path: Path) -> None:
    config = AuthConfig(
        client_id="client",
        authority="https://login.microsoftonline.com/common",
        scopes=["user.read"],
        cache_path=temp_cache_path,
    )

    with mock.patch.object(MSALClient, "_load_cache"):
        client = MSALClient(config)

    with mock.patch.object(client.app, "get_accounts", return_value=[]):
        assert client.acquire_token_silent() is None


def test_persist_cache_writes_file_and_keyring(temp_cache_path: Path) -> None:
    config = AuthConfig(
        client_id="client",
        authority="https://login.microsoftonline.com/common",
        scopes=["user.read"],
        cache_path=temp_cache_path,
    )

    with mock.patch.object(MSALClient, "_load_cache"):
        client = MSALClient(config)

    serialized = "serialized-cache"
    with mock.patch.object(client._cache, "has_state_changed", new=True), mock.patch.object(
        client._cache, "serialize", return_value=serialized
    ), mock.patch("keyring.set_password") as set_password:
        client._persist_cache()

    assert temp_cache_path.read_text(encoding="utf-8") == serialized
    set_password.assert_called_once()


def test_acquire_token_device_flow_initiates_flow(temp_cache_path: Path) -> None:
    config = AuthConfig(
        client_id="client",
        authority="https://login.microsoftonline.com/common",
        scopes=["user.read"],
        cache_path=temp_cache_path,
    )

    with mock.patch.object(MSALClient, "_load_cache"):
        client = MSALClient(config)

    with mock.patch.object(client.app, "initiate_device_flow", return_value={"user_code": "123"}) as initiate:
        flow = client.acquire_token_device_flow(prompt="login")

    assert flow is not None
    assert flow["user_code"] == "123"
    initiate.assert_called_once_with(scopes=config.scopes, prompt="login")

