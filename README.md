# OneDrive Linux Sync

Selective OneDrive sync client for Linux featuring a PySide6 desktop UI, Microsoft Graph authentication, and background scheduling via systemd.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .[dev]
```

## Headless Sync & Scheduling

- Run manual sync: `python -m scripts.cli sync-all`
- Install systemd timer:
  ```bash
  python -m scripts.cli install-systemd
  systemctl --user daemon-reload
  systemctl --user enable --now onedrive-sync.timer
  ```

## Development Tasks
- See `implementation_plan.md` for the roadmap.

