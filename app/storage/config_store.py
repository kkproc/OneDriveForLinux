"""SQLite-backed configuration store for sync metadata."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, UniqueConstraint, create_engine, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


DEFAULT_ACCOUNT_ID = "default"


class Base(DeclarativeBase):
    pass


def _normalize_account_id(account_id: Optional[str]) -> str:
    return account_id or DEFAULT_ACCOUNT_ID


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    username: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    tenant_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    account_type: Mapped[str] = mapped_column(String, default="unknown")
    authority: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    environment: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class SyncedFolder(Base):
    __tablename__ = "synced_folders"
    __table_args__ = (
        UniqueConstraint("account_id", "remote_id", name="uq_synced_folder_account_remote"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(String, ForeignKey("accounts.id"), nullable=False, index=True)
    remote_id: Mapped[str] = mapped_column(String, nullable=False)
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
    account_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("accounts.id"), nullable=True, index=True)


class SyncedItem(Base):
    __tablename__ = "synced_items"
    __table_args__ = (
        UniqueConstraint("account_id", "folder_remote_id", "relative_path", name="uq_synced_item_path"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(String, ForeignKey("accounts.id"), nullable=False, index=True)
    folder_remote_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    item_id: Mapped[str] = mapped_column(String, nullable=False)
    relative_path: Mapped[str] = mapped_column(String, nullable=False)
    etag: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    last_modified: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    local_mtime: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    content_hash: Mapped[Optional[str]] = mapped_column(String, nullable=True)


def ensure_schema(engine) -> None:
    with engine.connect() as conn:
        result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='accounts'"))
        if result.fetchone() is None:
            conn.execute(
                text(
                    """
                    CREATE TABLE accounts (
                        id TEXT PRIMARY KEY,
                        username TEXT UNIQUE NOT NULL,
                        display_name TEXT NOT NULL,
                        tenant_id TEXT,
                        account_type TEXT DEFAULT 'unknown',
                        authority TEXT,
                        environment TEXT,
                        last_login_at TEXT
                    )
                    """
                )
            )
        conn.execute(
            text(
                """
                INSERT OR IGNORE INTO accounts (id, username, display_name, account_type)
                VALUES (:id, :username, :display_name, 'personal')
                """
            ),
            {"id": DEFAULT_ACCOUNT_ID, "username": "default", "display_name": "Default Account"},
        )
        result = conn.execute(text("PRAGMA table_info('synced_folders')"))
        columns = {row[1] for row in result}
        if "account_id" not in columns:
            conn.execute(text("ALTER TABLE synced_folders ADD COLUMN account_id TEXT DEFAULT 'default'"))
            conn.execute(text("UPDATE synced_folders SET account_id = 'default' WHERE account_id IS NULL"))
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
        if "account_id" not in item_columns:
            conn.execute(text("ALTER TABLE synced_items ADD COLUMN account_id TEXT DEFAULT 'default'"))
            conn.execute(text("UPDATE synced_items SET account_id = 'default' WHERE account_id IS NULL"))
        if "content_hash" not in item_columns:
            conn.execute(text("ALTER TABLE synced_items ADD COLUMN content_hash TEXT"))
        result = conn.execute(text("PRAGMA table_info('preferences')"))
        pref_columns = {row[1] for row in result}
        if "account_id" not in pref_columns:
            conn.execute(text("ALTER TABLE preferences ADD COLUMN account_id TEXT"))
        result = conn.execute(text("PRAGMA table_info('sync_history')"))
        history_columns = {row[1] for row in result}
        if "account_id" not in history_columns:
            conn.execute(text("ALTER TABLE sync_history ADD COLUMN account_id TEXT DEFAULT 'default'"))
            conn.execute(text("UPDATE sync_history SET account_id = 'default' WHERE account_id IS NULL"))
        result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='sync_history'"))
        if result.fetchone() is None:
            conn.execute(
                text(
                    """
                    CREATE TABLE sync_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        account_id TEXT NOT NULL,
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
class AccountRecord:
    id: str
    username: str
    display_name: str
    tenant_id: Optional[str] = None
    account_type: str = "unknown"
    authority: Optional[str] = None
    environment: Optional[str] = None
    last_login_at: Optional[datetime] = None


@dataclass(slots=True)
class FolderConfig:
    account_id: str
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
    account_id: str
    folder_remote_id: str
    item_id: str
    relative_path: Path
    etag: Optional[str]
    last_modified: Optional[str]
    local_mtime: Optional[float]
    content_hash: Optional[str]


@dataclass(slots=True)
class SyncHistoryRecord:
    account_id: str
    folder_remote_id: str
    finished_at: datetime
    status: str
    error_message: Optional[str]


class SyncHistory(Base):
    __tablename__ = "sync_history"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(String, ForeignKey("accounts.id"), nullable=False, index=True)
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

    def upsert_account(self, account: AccountRecord) -> AccountRecord:
        with self.session() as session:
            row = session.get(Account, account.id)
            normalized_login = _ensure_optional_utc(account.last_login_at)
            if row:
                row.username = account.username
                row.display_name = account.display_name
                row.tenant_id = account.tenant_id
                row.account_type = account.account_type
                row.authority = account.authority
                row.environment = account.environment
                row.last_login_at = normalized_login
            else:
                session.add(
                    Account(
                        id=account.id,
                        username=account.username,
                        display_name=account.display_name,
                        tenant_id=account.tenant_id,
                        account_type=account.account_type,
                        authority=account.authority,
                        environment=account.environment,
                        last_login_at=normalized_login,
                    )
                )
        return account

    def get_account(self, account_id: str) -> Optional[AccountRecord]:
        resolved = _normalize_account_id(account_id)
        with Session(self.engine, future=True) as session:
            row = session.get(Account, resolved)
        return self._row_to_account(row) if row else None

    def get_accounts(self) -> list[AccountRecord]:
        with Session(self.engine, future=True) as session:
            rows = session.query(Account).order_by(Account.display_name.asc()).all()
        return [self._row_to_account(row) for row in rows]

    def remove_account(self, account_id: str, *, cascade: bool = False) -> None:
        resolved = _normalize_account_id(account_id)
        with self.session() as session:
            account = session.get(Account, resolved)
            if not account:
                return
            if cascade:
                session.query(SyncedItem).filter(SyncedItem.account_id == resolved).delete()
                session.query(SyncHistory).filter(SyncHistory.account_id == resolved).delete()
                session.query(SyncedFolder).filter(SyncedFolder.account_id == resolved).delete()
            session.delete(account)

    def upsert_folder(self, config: FolderConfig) -> FolderConfig:
        config.account_id = _normalize_account_id(config.account_id)
        with self.session() as session:
            existing = session.scalar(
                select(SyncedFolder).where(
                    (SyncedFolder.account_id == config.account_id)
                    & (SyncedFolder.remote_id == config.remote_id)
                )
            )
            normalized_synced_at = _ensure_optional_utc(config.last_synced_at)
            if existing:
                existing.account_id = config.account_id
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
                        account_id=config.account_id,
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

    def get_folder(self, account_id: str, remote_id: str) -> Optional[FolderConfig]:
        resolved = _normalize_account_id(account_id)
        with Session(self.engine, future=True) as session:
            row = session.scalar(
                select(SyncedFolder).where(
                    (SyncedFolder.account_id == resolved)
                    & (SyncedFolder.remote_id == remote_id)
                )
            )
        return self._row_to_config(row) if row else None

    def get_folders(self, account_id: Optional[str] = None) -> list[FolderConfig]:
        with Session(self.engine, future=True) as session:
            stmt = select(SyncedFolder)
            if account_id:
                stmt = stmt.where(SyncedFolder.account_id == _normalize_account_id(account_id))
            rows = session.scalars(stmt).all()
        return [self._row_to_config(row) for row in rows]

    def _row_to_config(self, row: SyncedFolder) -> FolderConfig:
        return FolderConfig(
            account_id=row.account_id,
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
        account_id: str,
        remote_id: str,
        *,
        delta_link: Optional[str] = None,
        last_synced_at: Optional[datetime] = None,
        last_status: Optional[str] = None,
        last_error: Optional[str] = None,
    ) -> None:
        resolved = _normalize_account_id(account_id)
        with self.session() as session:
            folder = session.scalar(
                select(SyncedFolder).where(
                    (SyncedFolder.account_id == resolved)
                    & (SyncedFolder.remote_id == remote_id)
                )
            )
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
        account_id: str,
        folder_remote_id: str,
        status: str,
        *,
        finished_at: Optional[datetime] = None,
        error_message: Optional[str] = None,
    ) -> None:
        resolved = _normalize_account_id(account_id)
        timestamp = _ensure_utc(finished_at or datetime.now(timezone.utc))
        with self.session() as session:
            session.add(
                SyncHistory(
                    account_id=resolved,
                    folder_remote_id=folder_remote_id,
                    finished_at=timestamp,
                    status=status,
                    error_message=error_message,
                )
            )

    def get_recent_history(self, account_id: str, remote_id: str, limit: int = 10) -> list[SyncHistoryRecord]:
        resolved = _normalize_account_id(account_id)
        with Session(self.engine, future=True) as session:
            rows = (
                session.query(SyncHistory)
                .where(
                    (SyncHistory.account_id == resolved)
                    & (SyncHistory.folder_remote_id == remote_id)
                )
                .order_by(SyncHistory.finished_at.desc())
                .limit(limit)
                .all()
            )
        return [
            SyncHistoryRecord(
                account_id=row.account_id,
                folder_remote_id=row.folder_remote_id,
                finished_at=_ensure_utc(row.finished_at),
                status=row.status,
                error_message=row.error_message,
            )
            for row in rows
        ]

    def get_latest_account_history(self, account_id: str) -> Optional[SyncHistoryRecord]:
        resolved = _normalize_account_id(account_id)
        with Session(self.engine, future=True) as session:
            row = (
                session.query(SyncHistory)
                .where(SyncHistory.account_id == resolved)
                .order_by(SyncHistory.finished_at.desc())
                .limit(1)
                .one_or_none()
            )
        if not row:
            return None
        return SyncHistoryRecord(
            account_id=row.account_id,
            folder_remote_id=row.folder_remote_id,
            finished_at=_ensure_utc(row.finished_at),
            status=row.status,
            error_message=row.error_message,
        )

    def update_folder_preferences(
        self,
        account_id: str,
        remote_id: str,
        *,
        sync_direction: Optional[str] = None,
        conflict_policy: Optional[str] = None,
    ) -> None:
        resolved = _normalize_account_id(account_id)
        with self.session() as session:
            folder = session.scalar(
                select(SyncedFolder).where(
                    (SyncedFolder.account_id == resolved)
                    & (SyncedFolder.remote_id == remote_id)
                )
            )
            if not folder:
                msg = f"Folder with remote_id {remote_id} not found"
                raise ValueError(msg)
            if sync_direction is not None:
                folder.sync_direction = sync_direction
            if conflict_policy is not None:
                folder.conflict_policy = conflict_policy

    def remove_folder(self, account_id: str, remote_id: str) -> None:
        resolved = _normalize_account_id(account_id)
        with self.session() as session:
            folder = session.scalar(
                select(SyncedFolder).where(
                    (SyncedFolder.account_id == resolved)
                    & (SyncedFolder.remote_id == remote_id)
                )
            )
            if folder:
                session.query(SyncedItem).filter(
                    (SyncedItem.account_id == resolved)
                    & (SyncedItem.folder_remote_id == remote_id)
                ).delete()
                session.delete(folder)

    def _row_to_account(self, row: Account) -> AccountRecord:
        return AccountRecord(
            id=row.id,
            username=row.username,
            display_name=row.display_name,
            tenant_id=row.tenant_id,
            account_type=row.account_type,
            authority=row.authority,
            environment=row.environment,
            last_login_at=_ensure_optional_utc(row.last_login_at),
        )

    def set_preference(self, key: str, value: str, *, account_id: Optional[str] = None) -> None:
        normalized = _normalize_account_id(account_id) if account_id else None
        pref_key = f"{normalized}:{key}" if normalized else key
        with self.session() as session:
            stmt = select(Preference).where(Preference.key == pref_key)
            pref = session.scalar(stmt)
            if pref:
                pref.value = value
            else:
                session.add(Preference(key=pref_key, value=value, account_id=normalized))

    def get_preference(self, key: str, *, account_id: Optional[str] = None) -> Optional[str]:
        normalized = _normalize_account_id(account_id) if account_id else None
        pref_key = f"{normalized}:{key}" if normalized else key
        with Session(self.engine, future=True) as session:
            pref = session.scalar(select(Preference).where(Preference.key == pref_key))
            return pref.value if pref else None

    def upsert_file_state(
        self,
        account_id: str,
        folder_remote_id: str,
        item_id: str,
        relative_path: Path,
        *,
        etag: Optional[str],
        last_modified: Optional[str],
        local_mtime: Optional[float],
        content_hash: Optional[str],
    ) -> None:
        resolved = _normalize_account_id(account_id)
        relative_str = str(relative_path)
        with self.session() as session:
            state = session.scalar(
                select(SyncedItem).where(
                    (SyncedItem.account_id == resolved)
                    & (SyncedItem.folder_remote_id == folder_remote_id)
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
                        account_id=resolved,
                        folder_remote_id=folder_remote_id,
                        item_id=item_id,
                        relative_path=relative_str,
                        etag=etag,
                        last_modified=last_modified,
                        local_mtime=local_mtime,
                        content_hash=content_hash,
                    )
                )

    def get_file_state(self, account_id: str, folder_remote_id: str, relative_path: Path) -> Optional[FileState]:
        resolved = _normalize_account_id(account_id)
        with Session(self.engine, future=True) as session:
            state = session.scalar(
                select(SyncedItem).where(
                    (SyncedItem.account_id == resolved)
                    & (SyncedItem.folder_remote_id == folder_remote_id)
                    & (SyncedItem.relative_path == str(relative_path))
                )
            )
            if not state:
                return None
            return FileState(
                account_id=resolved,
                folder_remote_id=state.folder_remote_id,
                item_id=state.item_id,
                relative_path=Path(state.relative_path),
                etag=state.etag,
                last_modified=state.last_modified,
                local_mtime=state.local_mtime,
                content_hash=state.content_hash,
            )

    def iter_file_states(self, account_id: str, folder_remote_id: str) -> list[FileState]:
        resolved = _normalize_account_id(account_id)
        with Session(self.engine, future=True) as session:
            rows = session.scalars(
                select(SyncedItem).where(
                    (SyncedItem.account_id == resolved)
                    & (SyncedItem.folder_remote_id == folder_remote_id)
                )
            ).all()
        return [
            FileState(
                account_id=resolved,
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

    def remove_file_state(self, account_id: str, folder_remote_id: str, relative_path: Path) -> None:
        resolved = _normalize_account_id(account_id)
        with self.session() as session:
            session.query(SyncedItem).filter(
                (SyncedItem.account_id == resolved)
                & (SyncedItem.folder_remote_id == folder_remote_id)
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
