# Architecture Plan

## Overview
- Desktop application built with Python, leveraging PySide6 for the UI and MSAL + Microsoft Graph for OneDrive access.
- Designed with modular packages to isolate UI, authentication, Graph API access, syncing, storage, and background scheduling concerns.
- Operates lazily: UI requests drive metadata and folder listings on demand, without pre-building a full index.

## Core Components
- `auth` – Handles MS Graph OAuth via MSAL public client with browser-based flow; persists refresh tokens securely using system keyring.
- `graph` – Thin asynchronous client using `httpx` to access Graph endpoints for drives, items, delta queries, and download/upload operations.
- `storage` – SQLite-backed configuration store for selected remote folders, mapped local destinations, delta tokens, sync metadata, and user preferences; also includes encrypted token cache connectors.
- `ui` – PySide6 layer split into view models and QML/Qt Widgets; mediates user interactions, triggers lazy data fetches, surfaces sync status, and lets the user pick local sync roots per remote folder.
- `sync` – Engine managing incremental, bidirectional folder sync tasks, leveraging Graph delta links, local file hashing, and filesystem watchers to detect remote and local mutations.
- `services` – Scheduler/orchestrator for periodic execution (via systemd timer) and inter-process coordination between UI and headless sync runs.

## Data Flow
1. User launches UI; app checks for stored tokens and initiates MSAL login if absent.
2. UI requests drive list and folder children through `graph` client; responses stream into view models without bulk caching.
3. Selected folders are stored in `storage`, including remote IDs, chosen local target paths, last-known etags, and delta tokens.
4. Sync engine runs (manual trigger or systemd timer), retrieves delta for each folder, compares with local filesystem changes (hashes, mtimes, watcher events), applies bidirectional updates, and updates metadata.
5. Sync results and errors propagate back to storage and are surfaced in the UI upon next load.

## Local Folder Selection & Sync Rules
- Each remote folder selection prompts the user to choose (or create) a corresponding local directory; mappings are persisted and validated before sync.
- UI enforces granular selection—no full-drive sync—ensuring only explicit folders participate.
- Sync engine honors per-folder include/exclude settings and protects against accidental mass deletions by requiring confirmation when large change sets are detected.

## Background Execution Strategy
- Provide a CLI entry-point `onedrive-sync` that can be invoked by both the UI (manual sync) and systemd service.
- Systemd user service runs the sync script every 10 minutes, reusing the shared SQLite config and token cache.
- Sync process writes structured logs; UI tails recent entries for display.

## Security & Credential Handling
- Tokens stored via MSAL token cache encrypted with OS keyring.
- Config database stores only necessary metadata; no plaintext secrets.
- OAuth client IDs/redirect URIs configurable via environment or config file.

## Concurrency & IPC Considerations
- Use file-based locking or SQLite advisory locks to prevent concurrent syncs on same folder.
- UI communicates with sync process through the shared database and optional DBus notifications for status updates.

## Extensibility Notes
- Additional cloud providers could integrate by implementing Graph-like adapters.
- Background scheduler could offer alternative backends (cron, launchd) in future.
- UI theming isolated in stylesheets/QML to support branding changes.

