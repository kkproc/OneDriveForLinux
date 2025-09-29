"""Microsoft authentication helpers."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import keyring
import msal
from keyring.errors import KeyringError


@dataclass
class AuthConfig:
    client_id: str
    authority: str
    scopes: list[str]
    cache_path: Optional[Path] = None
    keyring_service: str = "onedrive-linux-sync"
    keyring_account: Optional[str] = None

    def resolved_account(self) -> str:
        return self.keyring_account or self.client_id


class MSALClient:
    """Wrapper around MSAL PublicClientApplication with persistent cache."""

    def __init__(self, config: AuthConfig) -> None:
        self.config = config
        self._cache = msal.SerializableTokenCache()
        self._load_cache()
        self.app = msal.PublicClientApplication(
            client_id=config.client_id,
            authority=config.authority,
            token_cache=self._cache,
        )

    def acquire_token_interactive(self, *, login_hint: Optional[str] = None) -> Dict[str, Any]:
        extra: Dict[str, Any] = {}
        if login_hint:
            extra["login_hint"] = login_hint
        result = self.app.acquire_token_interactive(scopes=self.config.scopes, **extra)
        self._persist_cache()
        return result

    def acquire_token_device_flow(self, *, prompt: Optional[str] = None, login_hint: Optional[str] = None) -> Dict[str, Any]:
        extra_args = {}
        if prompt:
            extra_args["prompt"] = prompt
        if login_hint:
            extra_args["login_hint"] = login_hint
        flow = self.app.initiate_device_flow(scopes=self.config.scopes, **extra_args)
        if "user_code" not in flow:
            msg = "Failed to create device flow"
            raise RuntimeError(msg)
        return flow

    def poll_device_flow(self, flow: Dict[str, Any]) -> Dict[str, Any]:
        result = self.app.acquire_token_by_device_flow(flow)
        self._persist_cache()
        return result

    def acquire_token_silent(self, account_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        accounts = self._resolve_accounts(account_id)
        if not accounts:
            return None
        account = accounts[0]
        result = self.app.acquire_token_silent(self.config.scopes, account=account)
        if result:
            self._persist_cache()
        return result

    def get_accounts(self) -> list[Dict[str, Any]]:
        return list(self.app.get_accounts())

    def remove_account(self, account_id: str) -> None:
        matches = [acct for acct in self.app.get_accounts() if acct.get("home_account_id") == account_id]
        for account in matches:
            self.app.remove_account(account)
        if matches:
            self._persist_cache()

    def persist_cache_for(self, keyring_account: str, *, cache_path: Optional[Path] = None) -> None:
        serialized = self._cache.serialize()

        with suppress(KeyringError):
            keyring.set_password(self.config.keyring_service, keyring_account, serialized)

        target_path = cache_path or self.config.cache_path
        if target_path:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(serialized, encoding="utf-8")

    def clear_cache(self) -> None:
        """Remove cached credentials from keyring and disk."""

        self._cache.clear()
        with suppress(KeyringError):
            keyring.delete_password(
                self.config.keyring_service, self.config.resolved_account()
            )
        if self.config.cache_path:
            with suppress(FileNotFoundError):
                self.config.cache_path.unlink()

    def _load_cache(self) -> None:
        serialized_cache: Optional[str] = None

        with suppress(KeyringError):
            serialized_cache = keyring.get_password(
                self.config.keyring_service, self.config.resolved_account()
            )

        if not serialized_cache and self.config.cache_path:
            if self.config.cache_path.exists():
                serialized_cache = self.config.cache_path.read_text(encoding="utf-8")

        if serialized_cache:
            self._cache.deserialize(serialized_cache)

    def _persist_cache(self) -> None:
        if not self._cache.has_state_changed:
            return

        serialized = self._cache.serialize()

        with suppress(KeyringError):
            keyring.set_password(
                self.config.keyring_service, self.config.resolved_account(), serialized
            )

        if self.config.cache_path:
            self.config.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.config.cache_path.write_text(serialized, encoding="utf-8")

    def _resolve_accounts(self, account_id: Optional[str]) -> list[Dict[str, Any]]:
        accounts: Iterable[Dict[str, Any]] = self.app.get_accounts()
        if account_id:
            matches = [acct for acct in accounts if acct.get("home_account_id") == account_id]
            if matches:
                return matches
        return list(accounts)

