"""Command-line utilities for OneDrive Linux Sync."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import typer

from app.auth.msal_client import AuthConfig, MSALClient
from app.storage.config_store import ConfigStore, FolderConfig
from app.ui.app import run as run_ui

app = typer.Typer(help="Manage authentication and selective OneDrive folders.")
folders_app = typer.Typer(help="Manage synced folder mappings.")
app.add_typer(folders_app, name="folders")


DEFAULT_AUTHORITY = "https://login.microsoftonline.com/common"
DEFAULT_SCOPES = [
    "Files.ReadWrite.All",
    "User.Read",
]
DEFAULT_CACHE_PATH = Path("~/.cache/onedrive-linux-sync/token_cache.json").expanduser()
DEFAULT_DB_PATH = Path("~/.local/share/onedrive-linux-sync/config.db").expanduser()


def create_auth_client(
    client_id: str,
    authority: str,
    scopes: List[str],
    cache_path: Path,
) -> MSALClient:
    config = AuthConfig(
        client_id=client_id,
        authority=authority,
        scopes=scopes,
        cache_path=cache_path,
    )
    return MSALClient(config)


def get_config_store(db_path: Path) -> ConfigStore:
    return ConfigStore(db_path)


def _resolve_scopes(user_scopes: Optional[List[str]]) -> List[str]:
    if not user_scopes:
        return list(DEFAULT_SCOPES)
    # Typer passes repeated --scope values as list of strings.
    return list(user_scopes)


@app.command()
def login(
    client_id: str = typer.Option(..., "--client-id", envvar="ONEDRIVE_CLIENT_ID", prompt=True),
    authority: str = typer.Option(
        DEFAULT_AUTHORITY,
        "--authority",
        envvar="ONEDRIVE_AUTHORITY",
        help="AAD authority URL.",
    ),
    scope: Optional[List[str]] = typer.Option(
        None,
        "--scope",
        help="Microsoft Graph scope to request (repeat for multiple).",
        metavar="SCOPE",
    ),
    cache_path: Path = typer.Option(
        DEFAULT_CACHE_PATH,
        "--cache-path",
        help="Location for serialized MSAL cache.",
        show_default=True,
    ),
) -> None:
    """Authenticate with Microsoft Graph using device code flow when required."""

    scopes = _resolve_scopes(scope)
    client = create_auth_client(client_id, authority, scopes, cache_path)

    silent_result = client.acquire_token_silent()
    if silent_result:
        typer.secho("Already authenticated.", fg=typer.colors.GREEN)
        return

    flow = client.acquire_token_device_flow()
    verification_uri = flow.get("verification_uri")
    complete_uri = flow.get("verification_uri_complete")
    user_code = flow.get("user_code")

    typer.echo("Follow these steps to complete login:")
    if verification_uri and user_code:
        typer.echo(f"  1. Visit: {verification_uri}")
        typer.echo(f"  2. Enter code: {user_code}")
    if complete_uri:
        typer.echo(f"     (or open: {complete_uri})")
    typer.echo("Waiting for confirmation... press Ctrl+C to cancel")

    try:
        result = client.poll_device_flow(flow)
    except KeyboardInterrupt as exc:
        typer.secho("Login cancelled.", fg=typer.colors.YELLOW)
        raise typer.Exit(1) from exc

    if "access_token" in result:
        typer.secho("Login successful.", fg=typer.colors.GREEN)
    else:
        error_message = result.get("error_description") or "Unknown error"
        typer.secho(f"Login failed: {error_message}", fg=typer.colors.RED)
        raise typer.Exit(1)


@app.command()
def logout(
    client_id: str = typer.Option(..., "--client-id", envvar="ONEDRIVE_CLIENT_ID", prompt=True),
    authority: str = typer.Option(
        DEFAULT_AUTHORITY,
        "--authority",
        envvar="ONEDRIVE_AUTHORITY",
        help="AAD authority URL.",
    ),
    scope: Optional[List[str]] = typer.Option(
        None,
        "--scope",
        help="Microsoft Graph scope (for cache resolution).",
        metavar="SCOPE",
    ),
    cache_path: Path = typer.Option(
        DEFAULT_CACHE_PATH,
        "--cache-path",
        help="Location for serialized MSAL cache.",
    ),
) -> None:
    """Clear cached authentication credentials."""

    scopes = _resolve_scopes(scope)
    client = create_auth_client(client_id, authority, scopes, cache_path)
    client.clear_cache()
    typer.secho("Cached credentials removed.", fg=typer.colors.GREEN)


@app.command("token-status")
def token_status(
    client_id: str = typer.Option(..., "--client-id", envvar="ONEDRIVE_CLIENT_ID", prompt=True),
    authority: str = typer.Option(
        DEFAULT_AUTHORITY,
        "--authority",
        envvar="ONEDRIVE_AUTHORITY",
        help="AAD authority URL.",
    ),
    scope: Optional[List[str]] = typer.Option(
        None,
        "--scope",
        help="Microsoft Graph scope (for cache resolution).",
        metavar="SCOPE",
    ),
    cache_path: Path = typer.Option(
        DEFAULT_CACHE_PATH,
        "--cache-path",
        help="Location for serialized MSAL cache.",
    ),
) -> None:
    """Show whether a valid cached token is available."""

    scopes = _resolve_scopes(scope)
    client = create_auth_client(client_id, authority, scopes, cache_path)
    silent_result = client.acquire_token_silent()
    if silent_result and "access_token" in silent_result:
        typer.secho("Access token available in cache.", fg=typer.colors.GREEN)
    else:
        typer.secho("No cached token found.", fg=typer.colors.YELLOW)


@app.command()
def ui() -> None:
    """Launch the graphical interface."""

    run_ui()


@folders_app.command("list")
def list_folders(
    db_path: Path = typer.Option(
        DEFAULT_DB_PATH,
        "--db-path",
        envvar="ONEDRIVE_DB_PATH",
        help="Path to the configuration database.",
        show_default=True,
    )
) -> None:
    """List synced folders and their local mappings."""

    store = get_config_store(db_path)
    folders = store.get_folders()
    if not folders:
        typer.echo("No folders configured.")
        return
    for folder in folders:
        typer.echo(
            f"- {folder.display_name} (remote: {folder.remote_id}, drive: {folder.drive_id})\n"
            f"    local -> {folder.local_path} | include_subfolders={folder.include_subfolders}"
        )


@folders_app.command("add")
def add_folder(
    remote_id: str = typer.Argument(..., help="Remote OneDrive item ID."),
    drive_id: str = typer.Option(..., "--drive-id", help="Drive ID containing the item."),
    display_name: str = typer.Option(
        None,
        "--name",
        help="Friendly name shown in UI. Defaults to remote ID.",
    ),
    local_path: Path = typer.Option(..., "--local-path", help="Local directory to sync to."),
    include_subfolders: bool = typer.Option(
        True,
        "--include-subfolders/--no-include-subfolders",
        help="Control sync of nested folders.",
    ),
    db_path: Path = typer.Option(
        DEFAULT_DB_PATH,
        "--db-path",
        envvar="ONEDRIVE_DB_PATH",
        help="Path to the configuration database.",
        show_default=True,
    ),
) -> None:
    """Add or update a synced folder mapping."""

    store = get_config_store(db_path)
    local_path.mkdir(parents=True, exist_ok=True)
    name = display_name or remote_id
    store.upsert_folder(
        FolderConfig(
            remote_id=remote_id,
            drive_id=drive_id,
            display_name=name,
            local_path=local_path,
            include_subfolders=include_subfolders,
        )
    )
    typer.secho(
        f"Configured folder '{name}' -> {local_path}",
        fg=typer.colors.GREEN,
    )


@folders_app.command("remove")
def remove_folder(
    remote_id: str = typer.Argument(..., help="Remote OneDrive item ID."),
    db_path: Path = typer.Option(
        DEFAULT_DB_PATH,
        "--db-path",
        envvar="ONEDRIVE_DB_PATH",
        help="Path to the configuration database.",
        show_default=True,
    ),
) -> None:
    """Remove a synced folder mapping."""

    store = get_config_store(db_path)
    store.remove_folder(remote_id)
    typer.secho(f"Removed folder mapping for {remote_id}", fg=typer.colors.GREEN)


def main() -> None:
    app()


if __name__ == "__main__":
    main()

