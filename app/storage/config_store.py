"""SQLite-backed configuration store for sync metadata."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from sqlalchemy import Boolean, DateTime, Float, String, create_engine, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


class Base(DeclarativeBase):
    pass


class SyncedFolder(Base):
    __tablename__ = "synced_folders"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    remote_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    drive_id: Mapped[str] = mapped_column(String, nullable=False)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    local_path: Mapped[str] = mapped_column(String, nullable=False)
    include_subfolders: Mapped[bool] = mapped_column(Boolean, default=True)
    sync_direction: Mapped[str] = mapped_column(String, default="pull")
    conflict_policy: Mapped[str] = mapped_column(String, default="remote_wins")
    delta_link: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class Preference(Base):
    __tablename__ = "preferences"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String, nullable=False)


class SyncedItem(Base):
    __tablename__ = "synced_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    folder_remote_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    item_id: Mapped[str] = mapped_column(String, nullable=False)
    relative_path: Mapped[str] = mapped_column(String, nullable=False)
    etag: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    last_modified: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    local_mtime: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    content_hash: Mapped[Optional[str]] = mapped_column(String, nullable=True)


def ensure_schema(engine) -> None:
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA table_info('synced_folders')"))
        columns = {row[1] for row in result}
        if "sync_direction" not in columns:
            conn.execute(text("ALTER TABLE synced_folders ADD COLUMN sync_direction TEXT DEFAULT 'pull'"))
        if "conflict_policy" not in columns:
            conn.execute(text("ALTER TABLE synced_folders ADD COLUMN conflict_policy TEXT DEFAULT 'remote_wins'"))
        if "last_status" not in columns:
            conn.execute(text("ALTER TABLE synced_folders ADD COLUMN last_status TEXT"))
        if "last_error" not in columns:
            conn.execute(text("ALTER TABLE synced_folders ADD COLUMN last_error TEXT"))
        result = conn.execute(text("PRAGMA table_info('synced_items')"))
        item_columns = {row[1] for row in result}
        if "content_hash" not in item_columns:
            conn.execute(text("ALTER TABLE synced_items ADD COLUMN content_hash TEXT"))
        result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='sync_history'"))
        if result.fetchone() is None:
            conn.execute(
                text(
                    """
                    CREATE TABLE sync_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        folder_remote_id TEXT NOT NULL,
                        finished_at TEXT NOT NULL,
                        status TEXT NOT NULL,
                        error_message TEXT
                    )
                    """
                )
            )
        conn.commit()


@dataclass(slots=True)
class FolderConfig:
    remote_id: str
    drive_id: str
    display_name: str
    local_path: Path
    include_subfolders: bool = True
    sync_direction: str = "pull"
    conflict_policy: str = "remote_wins"
    delta_link: Optional[str] = None
    last_synced_at: Optional[datetime] = None
    last_status: Optional[str] = None
    last_error: Optional[str] = None


@dataclass(slots=True)
class FileState:
    folder_remote_id: str
    item_id: str
    relative_path: Path
    etag: Optional[str]
    last_modified: Optional[str]
    local_mtime: Optional[float]
    content_hash: Optional[str]


@dataclass(slots=True)
class SyncHistoryRecord:
    folder_remote_id: str
    finished_at: datetime
    status: str
    error_message: Optional[str]


class SyncHistory(Base):
    __tablename__ = "sync_history"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    folder_remote_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class ConfigStore:
    """Persist sync configuration and metadata."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(f"sqlite:///{db_path}", future=True)
        Base.metadata.create_all(self.engine)
        ensure_schema(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        with Session(self.engine, future=True) as session:
            yield session
            session.commit()

    def upsert_folder(self, config: FolderConfig) -> FolderConfig:
        with self.session() as session:
            existing = session.scalar(
                select(SyncedFolder).where(SyncedFolder.remote_id == config.remote_id)
            )
            normalized_synced_at = _ensure_optional_utc(config.last_synced_at)
            if existing:
                existing.drive_id = config.drive_id
                existing.display_name = config.display_name
                existing.local_path = str(config.local_path)
                existing.include_subfolders = config.include_subfolders
                existing.sync_direction = config.sync_direction
                existing.conflict_policy = config.conflict_policy
                existing.delta_link = config.delta_link
                existing.last_synced_at = normalized_synced_at
            else:
                session.add(
                    SyncedFolder(
                        remote_id=config.remote_id,
                        drive_id=config.drive_id,
                        display_name=config.display_name,
                        local_path=str(config.local_path),
                        include_subfolders=config.include_subfolders,
                        sync_direction=config.sync_direction,
                        conflict_policy=config.conflict_policy,
                        delta_link=config.delta_link,
                        last_synced_at=normalized_synced_at,
                    )
                )
        return config

    def get_folder(self, remote_id: str) -> Optional[FolderConfig]:
        with Session(self.engine, future=True) as session:
            row = session.scalar(select(SyncedFolder).where(SyncedFolder.remote_id == remote_id))
        return self._row_to_config(row) if row else None

    def get_folders(self) -> list[FolderConfig]:
        with Session(self.engine, future=True) as session:
            rows = session.scalars(select(SyncedFolder)).all()
        return [self._row_to_config(row) for row in rows]

    def _row_to_config(self, row: SyncedFolder) -> FolderConfig:
        return FolderConfig(
            remote_id=row.remote_id,
            drive_id=row.drive_id,
            display_name=row.display_name,
            local_path=Path(row.local_path),
            include_subfolders=row.include_subfolders,
            sync_direction=row.sync_direction,
            conflict_policy=row.conflict_policy,
            delta_link=row.delta_link,
            last_synced_at=_ensure_optional_utc(row.last_synced_at),
            last_status=row.last_status,
            last_error=row.last_error,
        )

    def update_folder_state(
        self,
        remote_id: str,
        *,
        delta_link: Optional[str] = None,
        last_synced_at: Optional[datetime] = None,
        last_status: Optional[str] = None,
        last_error: Optional[str] = None,
    ) -> None:
        with self.session() as session:
            folder = session.scalar(select(SyncedFolder).where(SyncedFolder.remote_id == remote_id))
            if not folder:
                msg = f"Folder with remote_id {remote_id} not found"
                raise ValueError(msg)
            if delta_link is not None:
                folder.delta_link = delta_link
            if last_synced_at is not None:
                folder.last_synced_at = _ensure_utc(last_synced_at)
            if last_status is not None:
                folder.last_status = last_status
            if last_error is not None:
                folder.last_error = last_error

    def record_sync_event(
        self,
        folder_remote_id: str,
        status: str,
        *,
        finished_at: Optional[datetime] = None,
        error_message: Optional[str] = None,
    ) -> None:
        timestamp = _ensure_utc(finished_at or datetime.now(timezone.utc))
        with self.session() as session:
            session.add(
                SyncHistory(
                    folder_remote_id=folder_remote_id,
                    finished_at=timestamp,
                    status=status,
                    error_message=error_message,
                )
            )

    def get_recent_history(self, remote_id: str, limit: int = 10) -> list[SyncHistoryRecord]:
        with Session(self.engine, future=True) as session:
            rows = (
                session.query(SyncHistory)
                .where(SyncHistory.folder_remote_id == remote_id)
                .order_by(SyncHistory.finished_at.desc())
                .limit(limit)
                .all()
            )
        return [
            SyncHistoryRecord(
                folder_remote_id=row.folder_remote_id,
                finished_at=_ensure_utc(row.finished_at),
                status=row.status,
                error_message=row.error_message,
            )
            for row in rows
        ]

    def update_folder_preferences(
        self,
        remote_id: str,
        *,
        sync_direction: Optional[str] = None,
        conflict_policy: Optional[str] = None,
    ) -> None:
        with self.session() as session:
            folder = session.scalar(select(SyncedFolder).where(SyncedFolder.remote_id == remote_id))
            if not folder:
                msg = f"Folder with remote_id {remote_id} not found"
                raise ValueError(msg)
            if sync_direction is not None:
                folder.sync_direction = sync_direction
            if conflict_policy is not None:
                folder.conflict_policy = conflict_policy

    def remove_folder(self, remote_id: str) -> None:
        with self.session() as session:
            folder = session.scalar(select(SyncedFolder).where(SyncedFolder.remote_id == remote_id))
            if folder:
                session.query(SyncedItem).filter(SyncedItem.folder_remote_id == remote_id).delete()
                session.delete(folder)

    def set_preference(self, key: str, value: str) -> None:
        with self.session() as session:
            pref = session.scalar(select(Preference).where(Preference.key == key))
            if pref:
                pref.value = value
            else:
                session.add(Preference(key=key, value=value))

    def get_preference(self, key: str) -> Optional[str]:
        with Session(self.engine, future=True) as session:
            pref = session.scalar(select(Preference).where(Preference.key == key))
            return pref.value if pref else None

    def upsert_file_state(
        self,
        folder_remote_id: str,
        item_id: str,
        relative_path: Path,
        *,
        etag: Optional[str],
        last_modified: Optional[str],
        local_mtime: Optional[float],
        content_hash: Optional[str],
    ) -> None:
        relative_str = str(relative_path)
        with self.session() as session:
            state = session.scalar(
                select(SyncedItem).where(
                    (SyncedItem.folder_remote_id == folder_remote_id)
                    & (SyncedItem.relative_path == relative_str)
                )
            )
            if state:
                state.item_id = item_id
                state.etag = etag
                state.last_modified = last_modified
                state.local_mtime = local_mtime
                state.content_hash = content_hash
            else:
                session.add(
                    SyncedItem(
                        folder_remote_id=folder_remote_id,
                        item_id=item_id,
                        relative_path=relative_str,
                        etag=etag,
                        last_modified=last_modified,
                        local_mtime=local_mtime,
                        content_hash=content_hash,
                    )
                )

    def get_file_state(self, folder_remote_id: str, relative_path: Path) -> Optional[FileState]:
        with Session(self.engine, future=True) as session:
            state = session.scalar(
                select(SyncedItem).where(
                    (SyncedItem.folder_remote_id == folder_remote_id)
                    & (SyncedItem.relative_path == str(relative_path))
                )
            )
            if not state:
                return None
            return FileState(
                folder_remote_id=state.folder_remote_id,
                item_id=state.item_id,
                relative_path=Path(state.relative_path),
                etag=state.etag,
                last_modified=state.last_modified,
                local_mtime=state.local_mtime,
                content_hash=state.content_hash,
            )

    def iter_file_states(self, folder_remote_id: str) -> list[FileState]:
        with Session(self.engine, future=True) as session:
            rows = session.scalars(
                select(SyncedItem).where(SyncedItem.folder_remote_id == folder_remote_id)
            ).all()
        return [
            FileState(
                folder_remote_id=row.folder_remote_id,
                item_id=row.item_id,
                relative_path=Path(row.relative_path),
                etag=row.etag,
                last_modified=row.last_modified,
                local_mtime=row.local_mtime,
                content_hash=row.content_hash,
            )
            for row in rows
        ]

    def remove_file_state(self, folder_remote_id: str, relative_path: Path) -> None:
        with self.session() as session:
            session.query(SyncedItem).filter(
                (SyncedItem.folder_remote_id == folder_remote_id)
                & (SyncedItem.relative_path == str(relative_path))
            ).delete()


def _ensure_optional_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return _ensure_utc(value)


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
