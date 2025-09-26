"""Headless sync entrypoint for systemd or cron."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from app.auth.msal_client import AuthConfig, MSALClient
from app.logging_utils import setup_logging
from app.storage.config_store import ConfigStore
from app.sync.engine import SyncEngine


async def _run() -> None:
    import os
    from dotenv import load_dotenv

    load_dotenv()
    store = ConfigStore(Path("~/.local/share/onedrive-linux-sync/config.db").expanduser())
    cfg = AuthConfig(
        client_id=os.environ["ONEDRIVE_CLIENT_ID"],
        authority="https://login.microsoftonline.com/common",
        scopes=["Files.ReadWrite.All", "User.Read"],
        cache_path=Path("~/.cache/onedrive-linux-sync/token_cache.json").expanduser(),
    )
    msal_client = MSALClient(cfg)

    async def token_provider() -> str:
        result = msal_client.acquire_token_silent()
        if not result:
            flow = msal_client.acquire_token_device_flow()
            result = msal_client.poll_device_flow(flow)
        return result["access_token"]

    engine = SyncEngine(token_provider, store)
    try:
        await engine.run_headless()
    finally:
        await engine.close()


def main() -> None:
    setup_logging()
    asyncio.run(_run())


if __name__ == "__main__":
    main()

