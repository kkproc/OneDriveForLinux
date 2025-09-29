"""PySide6 main window for OneDrive Linux Sync."""

from __future__ import annotations

from pathlib import Path
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from app.storage.config_store import AccountRecord, ConfigStore, FolderConfig, SyncHistoryRecord
from app.ui.dialogs import AddAccountDialog
from app.ui.models import FolderNode, FolderTreeModel
from app.ui.settings_dialog import SettingsDialog


class MainWindow(QtWidgets.QMainWindow):
    load_children_requested = QtCore.Signal(object)
    selection_toggled = QtCore.Signal(object, bool, str, str, str)
    sync_requested = QtCore.Signal(object)
    history_requested = QtCore.Signal(str, str)
    account_created = QtCore.Signal(dict)
    account_switch_requested = QtCore.Signal(str)
    account_remove_requested = QtCore.Signal(str)

    def __init__(
        self,
        store: ConfigStore,
        active_account: AccountRecord,
        token_provider: Callable[[], Awaitable[str]] | None = None,
        device_flow_handler: Optional[Callable[[], tuple[dict, Callable[[], dict]]]] = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("OneDrive Selective Sync")
        self.resize(1200, 768)

        self.store = store
        self.active_account = active_account
        self.token_provider = token_provider
        self._device_flow_handler = device_flow_handler
        self._root_node = FolderNode(id="root", name="OneDrive", drive_id=None, is_folder=True)
        self._model = FolderTreeModel(self._root_node)
        self._selected_nodes: Dict[str, Tuple[FolderNode, Path, str, str]] = {}
        self._current_node: Optional[FolderNode] = None
        self._accounts: List[AccountRecord] = []
        self._status_icons: Dict[str, QtGui.QIcon] = {}
        self._suppress_account_change = False

        self._init_ui()
        self.folder_tree.setModel(self._model)
        self.folder_tree.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.folder_tree.setExpandsOnDoubleClick(False)
        self.folder_tree.expanded.connect(self._handle_expand)
        self.folder_tree.doubleClicked.connect(self._handle_double_click)
        self.folder_tree.selectionModel().selectionChanged.connect(self._handle_selection)

    def _init_ui(self) -> None:
        icon_path = Path(__file__).resolve().parent / "assets" / "onedrive.png"
        if icon_path.exists():
            self.setWindowIcon(QtGui.QIcon(str(icon_path)))

        toolbar = self.addToolBar("Main")
        toolbar.setMovable(False)
        sync_action = QtGui.QAction("Sync Now", self)
        sync_action.triggered.connect(self._trigger_current_sync)
        toolbar.addAction(sync_action)
        add_account_action = QtGui.QAction("Add Account", self)
        add_account_action.triggered.connect(self._show_add_account_dialog)
        toolbar.addAction(add_account_action)
        settings_action = QtGui.QAction("Settings", self)
        settings_action.triggered.connect(self._open_settings)
        toolbar.addAction(settings_action)

        self.status_bar = self.statusBar()
        self.status_bar.showMessage("Ready")
        self._status_indicator = QtWidgets.QLabel("Idle")
        font = self._status_indicator.font()
        font.setPointSize(font.pointSize() - 1)
        self._status_indicator.setFont(font)
        self._status_indicator.setStyleSheet("color: #9ca3af;")
        self.status_bar.addPermanentWidget(self._status_indicator)
        self._progress = QtWidgets.QProgressBar()
        self._progress.setMaximumWidth(150)
        self._progress.setRange(0, 0)
        self._progress.hide()
        self.status_bar.addPermanentWidget(self._progress)

        splitter = QtWidgets.QSplitter()
        splitter.setOrientation(QtCore.Qt.Horizontal)

        accounts_panel = QtWidgets.QWidget()
        accounts_layout = QtWidgets.QVBoxLayout(accounts_panel)
        accounts_layout.addWidget(QtWidgets.QLabel("Accounts"))
        self.account_list = QtWidgets.QListWidget()
        self.account_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.account_list.currentRowChanged.connect(self._handle_account_selection)
        accounts_layout.addWidget(self.account_list)
        self.remove_account_button = QtWidgets.QPushButton("Remove")
        self.remove_account_button.clicked.connect(self._remove_selected_account)
        accounts_layout.addWidget(self.remove_account_button)
        accounts_layout.addStretch(1)
        splitter.addWidget(accounts_panel)

        self.folder_tree = QtWidgets.QTreeView()
        self.folder_tree.setHeaderHidden(True)
        self.folder_tree.setStyleSheet("QTreeView { background: #1e1e1e; color: #d4d4d4; } QTreeView::item:selected { background: #264f78; }")
        splitter.addWidget(self.folder_tree)

        right_panel = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right_panel)

        self.details_label = QtWidgets.QLabel("Select a folder to see details")
        self.details_label.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft)
        right_layout.addWidget(self.details_label)

        self.select_button = QtWidgets.QPushButton("Select for Sync")
        self.select_button.setEnabled(False)
        self.select_button.clicked.connect(self._toggle_selection)
        right_layout.addWidget(self.select_button)

        path_layout = QtWidgets.QHBoxLayout()
        self.path_label = QtWidgets.QLabel("Local path: —")
        path_layout.addWidget(self.path_label)
        self.path_button = QtWidgets.QPushButton("Change…")
        self.path_button.setEnabled(False)
        self.path_button.clicked.connect(self._change_local_path)
        path_layout.addWidget(self.path_button)
        right_layout.addLayout(path_layout)

        self.sync_button = QtWidgets.QPushButton("Sync Now")
        self.sync_button.setEnabled(False)
        self.sync_button.clicked.connect(self._request_sync)
        right_layout.addWidget(self.sync_button)

        self.selected_list = QtWidgets.QListWidget()
        self.selected_list.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        right_layout.addWidget(QtWidgets.QLabel("Selected Folders"))
        right_layout.addWidget(self.selected_list, stretch=1)

        self.history_list = QtWidgets.QListWidget()
        self.history_list.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        right_layout.addWidget(QtWidgets.QLabel("Recent Sync History"))
        right_layout.addWidget(self.history_list, stretch=1)

        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 2)
        splitter.setStretchFactor(2, 2)

        self.setCentralWidget(splitter)
        self._update_remove_button_state()

    def set_accounts(self, accounts: List[AccountRecord]) -> None:
        self._accounts = accounts
        self._suppress_account_change = True
        self.account_list.blockSignals(True)
        self.account_list.clear()
        current_index = -1
        for index, account in enumerate(accounts):
            item = QtWidgets.QListWidgetItem()
            item.setData(QtCore.Qt.UserRole, account)
            self._apply_account_status(item, account)
            self.account_list.addItem(item)
            if account.id == self.active_account.id:
                current_index = index
        if current_index >= 0:
            self.account_list.setCurrentRow(current_index)
        elif self.account_list.count() > 0:
            self.account_list.setCurrentRow(0)
        self.account_list.blockSignals(False)
        self._suppress_account_change = False
        self._update_remove_button_state()

    def set_status(self, message: str, timeout: int = 3000) -> None:
        self.status_bar.showMessage(message, timeout)

    def set_sync_activity(self, message: str, *, success: bool | None = None) -> None:
        if success is None:
            self._status_indicator.setStyleSheet("color: #fbbf24;")
        elif success:
            self._status_indicator.setStyleSheet("color: #22c55e;")
        else:
            self._status_indicator.setStyleSheet("color: #ef4444;")
        self._status_indicator.setText(message)

    def apply_active_account(self, account: AccountRecord) -> None:
        self.active_account = account
        self.setWindowTitle(f"OneDrive Selective Sync – {account.display_name}")
        self._suppress_account_change = True
        for row in range(self.account_list.count()):
            item = self.account_list.item(row)
            item_account: AccountRecord = item.data(QtCore.Qt.UserRole)
            if item_account and item_account.id == account.id:
                item.setData(QtCore.Qt.UserRole, account)
                self._apply_account_status(item, account)
                self.account_list.setCurrentRow(row)
                break
        self._suppress_account_change = False
        self.history_list.clear()
        self._update_remove_button_state()

    def populate_root(self, children: List[FolderNode]) -> None:
        self._progress.show()
        self._model.set_root_children(children)
        for child in children:
            child.is_loading = False
        self._progress.hide()

    def set_selected_nodes(self, configs: List[FolderConfig]) -> None:
        self._selected_nodes.clear()
        for cfg in configs:
            node = FolderNode(
                id=cfg.remote_id,
                name=cfg.display_name,
                drive_id=cfg.drive_id,
                is_folder=True,
                fetched=True,
            )
            self._selected_nodes[cfg.remote_id] = (
                node,
                cfg.local_path,
                cfg.sync_direction,
                cfg.conflict_policy,
            )
        self._refresh_selected_list()
        self.history_list.clear()
        self._update_remove_button_state()

    def update_history(self, display_name: str, records: List[SyncHistoryRecord]) -> None:
        self.history_list.clear()
        title = f"Recent Sync History – {display_name}"
        header_item = QtWidgets.QListWidgetItem(title)
        header_item.setFlags(QtCore.Qt.NoItemFlags)
        self.history_list.addItem(header_item)
        if not records:
            empty_item = QtWidgets.QListWidgetItem("(No recent syncs)")
            empty_item.setFlags(QtCore.Qt.NoItemFlags)
            self.history_list.addItem(empty_item)
            return
        for record in records:
            timestamp = record.finished_at.strftime("%Y-%m-%d %H:%M:%S")
            message = f"{timestamp} • {record.status.upper()}"
            if record.error_message:
                message += f" – {record.error_message}"
            item = QtWidgets.QListWidgetItem(message)
            item.setFlags(QtCore.Qt.NoItemFlags)
            if record.status.lower() == "error":
                item.setForeground(QtGui.QBrush(QtGui.QColor("#ff6b6b")))
            self.history_list.addItem(item)

    def append_children(self, parent: FolderNode, children: List[FolderNode]) -> None:
        if not children:
            parent.fetched = True
            self._progress.hide()
            return
        self._model.insert_children(parent, children)
        parent.is_loading = False
        index = self._model.index_for_node(parent)
        if index.isValid():
            self.folder_tree.expand(index)
        if not any(node.is_loading for node in self._root_node.children):
            self._progress.hide()

    def _request_children(self, node: FolderNode) -> None:
        if not node.is_folder or node.is_loading or node.fetched:
            return
        node.is_loading = True
        self._progress.show()
        self.load_children_requested.emit(node)

    def _handle_expand(self, index: QtCore.QModelIndex) -> None:
        if not index.isValid():
            return
        node: FolderNode = index.internalPointer()
        self._request_children(node)

    def _handle_double_click(self, index: QtCore.QModelIndex) -> None:
        if not index.isValid():
            return
        node: FolderNode = index.internalPointer()
        if node.is_folder and not node.fetched:
            self._request_children(node)
            return
        elif node.is_folder:
            if self.folder_tree.isExpanded(index):
                self.folder_tree.collapse(index)
            else:
                self.folder_tree.expand(index)

    def _handle_selection(
        self, selected: QtCore.QItemSelection, _deselected: QtCore.QItemSelection
    ) -> None:
        if not selected.indexes():
            self._current_node = None
            self.select_button.setEnabled(False)
            self.sync_button.setEnabled(False)
            self.path_button.setEnabled(False)
            self.path_label.setText("Local path: —")
            return
        node: FolderNode = selected.indexes()[0].internalPointer()
        self._current_node = node
        details = [
            f"Name: {node.name}",
            f"ID: {node.id}",
            f"Drive ID: {node.drive_id or 'default'}",
            f"Type: {'Folder' if node.is_folder else 'File'}",
        ]
        self.details_label.setText("\n".join(details))
        self.select_button.setEnabled(node.is_folder)
        in_selected = node.id in self._selected_nodes
        self.select_button.setText("Remove from Sync" if in_selected else "Select for Sync")
        self.sync_button.setEnabled(in_selected)
        if in_selected:
            _, path, direction, conflict = self._selected_nodes[node.id]
            cfg = self.store.get_folder(self.active_account.id, node.id)
            status_line = "Status: —"
            if cfg and cfg.last_status:
                status_line = f"Status: {cfg.last_status}"
                if cfg.last_error:
                    status_line += f"\nError: {cfg.last_error}"
            self.path_label.setText(
                f"Local path: {path}\nDirection: {direction}\nConflict: {conflict}\n{status_line}"
            )
            self.path_button.setEnabled(True)
            display_name = cfg.display_name if cfg else node.name
            self.history_requested.emit(node.id, display_name)
        else:
            self.path_label.setText("Local path: —")
            self.path_button.setEnabled(False)
            self.history_list.clear()

    def _toggle_selection(self) -> None:
        indexes = self.folder_tree.selectionModel().selectedIndexes()
        if not indexes:
            return
        node: FolderNode = indexes[0].internalPointer()
        defaults_direction = (
            self.store.get_preference("default_direction", account_id=self.active_account.id)
            or "pull"
        )
        defaults_conflict = (
            self.store.get_preference("default_conflict", account_id=self.active_account.id)
            or "remote_wins"
        )
        currently_selected = node.id in self._selected_nodes
        if currently_selected:
            self._selected_nodes.pop(node.id, None)
            self.selection_toggled.emit(node, False, "", "", "")
            self.sync_button.setEnabled(False)
            self.path_button.setEnabled(False)
            self.path_label.setText("Local path: —")
            self.history_list.clear()
        else:
            default_dir = (Path.home() / "OneDriveSelective" / node.name).resolve()
            default_dir.parent.mkdir(parents=True, exist_ok=True)
            selected_dir = QtWidgets.QFileDialog.getExistingDirectory(
                self,
                "Choose local folder",
                str(default_dir.parent),
            )
            if not selected_dir:
                return
            path = Path(selected_dir)
            self._selected_nodes[node.id] = (node, path, defaults_direction, defaults_conflict)
            self.selection_toggled.emit(node, True, str(path), defaults_direction, defaults_conflict)
            self.sync_button.setEnabled(True)
            self.path_button.setEnabled(True)
            self.path_label.setText(f"Local path: {path}\nDirection: {defaults_direction}\nConflict: {defaults_conflict}")
            self.history_requested.emit(node.id, node.name)
        self._refresh_selected_list()
        self.select_button.setText("Remove from Sync" if node.id in self._selected_nodes else "Select for Sync")

    def _change_local_path(self) -> None:
        if not self._current_node or self._current_node.id not in self._selected_nodes:
            return
        node = self._current_node
        _, current_path, direction, conflict = self._selected_nodes[node.id]
        selected_dir = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Choose local folder",
            str(current_path),
        )
        if not selected_dir:
            return
        path = Path(selected_dir)
        self._selected_nodes[node.id] = (node, path, direction, conflict)
        self.path_label.setText(f"Local path: {path}\nDirection: {direction}\nConflict: {conflict}")
        self.selection_toggled.emit(node, True, str(path), direction, conflict)
        self.history_requested.emit(node.id, node.name)
        self._refresh_selected_list()

    def _request_sync(self) -> None:
        if self._current_node and self._current_node.id in self._selected_nodes:
            self.sync_requested.emit(self._current_node)

    def _refresh_selected_list(self) -> None:
        self.selected_list.clear()
        for node, path, direction, conflict in self._selected_nodes.values():
            self.selected_list.addItem(f"{node.name} → {path} ({direction}, {conflict})")

    def set_loading_complete(self) -> None:
        self._progress.hide()

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.store, self.active_account.id, self)
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            dialog.save()

    def update_account(self, account: AccountRecord, token_provider: Callable[[], Awaitable[str]] | None) -> None:
        self.active_account = account
        self.token_provider = token_provider

    def update_account(self, account: AccountRecord, token_provider: Callable[[], Awaitable[str]] | None) -> None:
        self.active_account = account
        self.token_provider = token_provider

    def _trigger_current_sync(self) -> None:
        if self._current_node and self._current_node.id in self._selected_nodes:
            self.sync_requested.emit(self._current_node)

    def _handle_account_selection(self, row: int) -> None:
        if self._suppress_account_change or row < 0:
            return
        item = self.account_list.item(row)
        if not item:
            return
        account: AccountRecord = item.data(QtCore.Qt.UserRole)
        if not account or account.id == self.active_account.id:
            self._update_remove_button_state()
            return
        self.account_switch_requested.emit(account.id)
        self._update_remove_button_state()

    def _remove_selected_account(self) -> None:
        item = self.account_list.currentItem()
        if not item or self.account_list.count() <= 1:
            return
        account: AccountRecord = item.data(QtCore.Qt.UserRole)
        if not account:
            return
        self.account_remove_requested.emit(account.id)

    def _update_remove_button_state(self) -> None:
        can_remove = self.account_list.count() > 1 and self.account_list.currentItem() is not None
        self.remove_account_button.setEnabled(can_remove)

    def refresh_account_status(self, account_id: str) -> None:
        account = self.store.get_account(account_id)
        if not account:
            return
        for row in range(self.account_list.count()):
            item = self.account_list.item(row)
            item_account: AccountRecord = item.data(QtCore.Qt.UserRole)
            if item_account and item_account.id == account_id:
                item.setData(QtCore.Qt.UserRole, account)
                self._apply_account_status(item, account)
                break

    def refresh_all_account_statuses(self) -> None:
        for row in range(self.account_list.count()):
            item = self.account_list.item(row)
            account: AccountRecord = item.data(QtCore.Qt.UserRole)
            if not account:
                continue
            latest = self.store.get_account(account.id)
            account_to_use = latest or account
            item.setData(QtCore.Qt.UserRole, account_to_use)
            self._apply_account_status(item, account_to_use)

    def _show_add_account_dialog(self) -> None:
        if not self._device_flow_handler:
            QtWidgets.QMessageBox.information(self, "Unavailable", "Device login is not configured for this build.")
            return
        dialog = AddAccountDialog(self, device_flow_handler=self._device_flow_handler)
        dialog.account_added.connect(self.account_created.emit)
        dialog.exec()

    def _apply_account_status(self, item: QtWidgets.QListWidgetItem, account: AccountRecord) -> None:
        record = self.store.get_latest_account_history(account.id)
        status_label = "No syncs"
        tooltip = "No sync history yet."
        color = QtGui.QColor("#6b7280")
        if record:
            when = record.finished_at.astimezone().strftime("%Y-%m-%d %H:%M")
            status_lower = record.status.lower()
            is_error = status_lower == "error"
            status_label = "Error" if is_error else "Synced"
            tooltip = f"Last sync: {status_label.lower()} at {when}"
            if record.error_message:
                tooltip += f"\n{record.error_message}"
            color = QtGui.QColor("#ef4444" if is_error else "#22c55e")
        item.setText(f"{account.display_name} ({status_label})")
        item.setData(QtCore.Qt.ToolTipRole, tooltip)
        item.setData(QtCore.Qt.StatusTipRole, tooltip)
        item.setData(QtCore.Qt.AccessibleDescriptionRole, tooltip)
        item.setIcon(self._status_icon(color))

    def _status_icon(self, color: QtGui.QColor) -> QtGui.QIcon:
        key = color.name()
        if key in self._status_icons:
            return self._status_icons[key]
        pixmap = QtGui.QPixmap(12, 12)
        pixmap.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(pixmap)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setBrush(QtGui.QBrush(color))
        pen = QtGui.QPen(QtGui.QColor("#1f2937"))
        pen.setWidth(1)
        painter.setPen(pen)
        painter.drawEllipse(1, 1, 10, 10)
        painter.end()
        icon = QtGui.QIcon(pixmap)
        self._status_icons[key] = icon
        return icon
