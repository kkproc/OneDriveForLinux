# Implementation Plan

## Phase 1 – Project Setup
- [x] Bootstrap a Python project targeting Linux desktop; create `.venv` and install core dependencies (`msal`, `httpx`, `PySide6`, `sqlalchemy`, `keyring`, `typer`, `pyqt6-styles`).
- [x] Establish module layout: `app/` with `ui/`, `auth/`, `graph/`, `sync/`, `storage/`, `services/`, plus `scripts/` for helper commands.
- [x] Configure pre-commit hooks (ruff, black, mypy) and CI scaffolding if desired.

## Phase 2 – Authentication & Configuration
- [x] Implement `auth/msal_client.py` wrapping MSAL public client, browser/device login, and token cache persistence via `keyring`.
- [x] Build config manager (`storage/config_store.py`) backed by SQLite to persist selected folder ids, local mapping, and sync schedules.
- [x] Add CLI commands to initiate login, logout, and list configured folders for debugging.

## Phase 3 – Microsoft Graph Integration
- [x] Implement `graph/onedrive_client.py` using `httpx` with retry/backoff to call Graph endpoints lazily (drive metadata, item listings, delta queries).
- [x] Add thin data models (pydantic or dataclasses) for drive items to normalize API responses for the rest of the app.
- [x] Provide pagination helpers that stream children for UI without building a full index (fetch page-on-demand).

## Phase 4 – UI Layer
- [x] Use PySide6 with QML or Qt Widgets to create a main window featuring navigation pane of drives, folder tree with on-demand expansion, and selection toggles.
- [x] Integrate styling (Qt Quick Controls 2 or custom stylesheet) for a modern, lightweight appearance.
- [x] Connect UI interactions to async data providers; ensure loading indicators during Graph fetches. _(progress indicator in status bar; further polish pending)_
- [x] Implement workflow for selecting remote folders and binding them to user-chosen local directories; validate mappings and surface sync summaries.
- [x] Implement settings view to manage login state, per-folder sync preferences (direction, conflict policy), and sync frequency (default 10 minutes).

## Phase 5 – Sync Scheduler & Engine
- [x] Develop `sync/engine.py` handling bidirectional sync per selected folder: compare remote metadata via delta queries, detect local changes via hashes and filesystem snapshots/watchers, and reconcile according to conflict rules. _(local hash tracking + conflict handling finalized)_
- [x] Ensure sync routines are incremental, chunked, and resume-safe; cache minimal state (last delta link, etags, content hashes) per folder. _(content hashes & delta persistence wired)_
- [x] Implement conflict resolution module managing local-vs-remote precedence, version history, and user prompts when necessary. _(prompt + headless fallbacks complete)_
- [x] Implement job orchestrator that can run headless, invoked by background service or UI-triggered manual sync. _(shared headless runner powering CLI and service)_

## Phase 6 – Background Service Integration
- [x] Create a dedicated entry-point script `scripts/run_sync.py` that executes pending sync jobs for all selected folders and exits.
- [ ] Provide instructions for installing a systemd user service and timer (`onedrive-sync.service` and `onedrive-sync.timer`) executing the script every 10 minutes using the project `.venv`. _(systemd install helper CLI in progress)_
- [ ] Add optional CLI helper to install/uninstall the systemd units automatically.

## Phase 7 – Observability & Resilience
- [ ] Implement structured logging with rotation (log to `~/.local/share/onedrive-sync/logs`).
- [ ] Surface recent sync status in the UI, including last run time, successes, and errors.
- [ ] Add notification hooks (DBus or libnotify) for notable events (auth expiry, sync failures).

## Phase 8 – Testing & QA
- [ ] Write unit tests for Graph client, auth flows (mocked), config store, and sync engine using pytest and responses.
- [ ] Add UI smoke tests leveraging `pytest-qt` if feasible; otherwise manual test checklist.
- [ ] Document manual test scenarios: login, folder selection, incremental sync, offline recovery.

## Phase 9 – Packaging & Distribution
- [ ] Create a launcher script/desktop entry for the UI integrating with freedesktop.
- [ ] Bundle requirements via `pip-tools` or `poetry export`; provide instructions for packaging as AppImage or Flatpak.
- [ ] Draft README with setup instructions, background service installation, troubleshooting, and MS Graph app registration steps.

