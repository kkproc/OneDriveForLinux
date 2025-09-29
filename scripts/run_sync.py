"""Headless sync entrypoint for systemd or cron."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Awaitable, Callable

from app.auth.msal_client import AuthConfig, MSALClient
from app.logging_utils import setup_logging
from app.storage.config_store import ConfigStore
from app.sync.engine import SyncEngine


async def _build_token_provider(account_id: str, client: MSALClient) -> Callable[[], Awaitable[str]]:
    async def _provider() -> str:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, client.acquire_token_silent, account_id)
        if not result:
            raise RuntimeError(f"No cached token for account {account_id}; run login first")
        client.persist_cache_for(account_id)
        return result["access_token"]

    def _persist_cache() -> None:
        client.persist_cache_for(account_id)

    _provider.persist_cache = _persist_cache  # type: ignore[attr-defined]
    return _provider


async def _run() -> None:
    import os
    from dotenv import load_dotenv

    load_dotenv()
    store = ConfigStore(Path("~/.local/share/onedrive-linux-sync/config.db").expanduser())
    accounts = store.get_accounts()
    if not accounts:
        logging.info("No accounts configured; skipping headless sync")
        return

    client_id = os.environ["ONEDRIVE_CLIENT_ID"]
    authority = os.environ.get("ONEDRIVE_AUTHORITY", "https://login.microsoftonline.com/common")
    scopes = ["Files.ReadWrite.All", "User.Read"]
    cache_path = Path("~/.cache/onedrive-linux-sync/token_cache.json").expanduser()

    for account in accounts:
        logging.info("Starting headless sync for account %s", account.display_name)
        cfg = AuthConfig(
            client_id=client_id,
            authority=authority,
            scopes=scopes,
            cache_path=cache_path,
            keyring_account=account.id,
        )
        msal_client = MSALClient(cfg)
        token_provider = await _build_token_provider(account.id, msal_client)

        try:
            await token_provider()
        except RuntimeError as exc:
            logging.warning("Skipping account %s: %s", account.display_name, exc)
            continue

        engine = SyncEngine(token_provider, store, account_id=account.id)
        try:
            await engine.run_headless()
        except Exception:  # pragma: no cover - guard against background failures
            logging.exception("Headless sync failed for account %s", account.display_name)
        else:
            logging.info("Headless sync completed for account %s", account.display_name)
        finally:
            await engine.close()



def main() -> None:
    setup_logging()
    asyncio.run(_run())


if __name__ == "__main__":
    main()
