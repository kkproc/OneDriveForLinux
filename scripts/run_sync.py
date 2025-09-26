"""Headless sync entrypoint for systemd or cron."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from app.auth.msal_client import AuthConfig, MSALClient
from app.storage.config_store import ConfigStore
from app.sync.engine import SyncEngine

LOG_PATH = Path("~/.local/share/onedrive-linux-sync/logs/run_sync.log").expanduser()


def setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()],
    )


async def _run() -> None:
    store = ConfigStore(Path("~/.local/share/onedrive-linux-sync/config.db").expanduser())
    cfg = AuthConfig(
        client_id=Path("~/.config/onedrive-linux-sync/client_id.txt").expanduser().read_text().strip(),
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

