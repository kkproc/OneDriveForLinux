"""Settings dialog for OneDrive Sync."""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from app.storage.config_store import ConfigStore


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, store: ConfigStore, account_id: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sync Settings")
        self.store = store
        self.account_id = account_id

        layout = QtWidgets.QVBoxLayout(self)

        self.frequency_spin = QtWidgets.QSpinBox()
        self.frequency_spin.setRange(1, 720)
        freq = store.get_preference("sync_frequency", account_id=account_id)
        self.frequency_spin.setValue(int(freq) if freq else 10)
        layout.addWidget(QtWidgets.QLabel("Sync frequency (minutes):"))
        layout.addWidget(self.frequency_spin)

        self.direction_combo = QtWidgets.QComboBox()
        self.direction_combo.addItems(["pull", "push", "bidirectional"])
        direction = store.get_preference("default_direction", account_id=account_id)
        if direction:
            index = self.direction_combo.findText(direction)
            if index >= 0:
                self.direction_combo.setCurrentIndex(index)
        layout.addWidget(QtWidgets.QLabel("Default sync direction:"))
        layout.addWidget(self.direction_combo)

        self.conflict_combo = QtWidgets.QComboBox()
        self.conflict_combo.addItems(["remote_wins", "local_wins", "prompt"])
        conflict = store.get_preference("default_conflict", account_id=account_id)
        if conflict:
            index = self.conflict_combo.findText(conflict)
            if index >= 0:
                self.conflict_combo.setCurrentIndex(index)
        layout.addWidget(QtWidgets.QLabel("Default conflict policy:"))
        layout.addWidget(self.conflict_combo)

        button_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def save(self) -> None:
        self.store.set_preference("sync_frequency", str(self.frequency_spin.value()), account_id=self.account_id)
        self.store.set_preference("default_direction", self.direction_combo.currentText(), account_id=self.account_id)
        self.store.set_preference("default_conflict", self.conflict_combo.currentText(), account_id=self.account_id)
