"""Settings dialog for OneDrive Sync."""

from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtWidgets

from app.storage.config_store import ConfigStore


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, store: ConfigStore, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sync Settings")
        self.store = store

        layout = QtWidgets.QVBoxLayout(self)

        self.frequency_spin = QtWidgets.QSpinBox()
        self.frequency_spin.setRange(1, 720)
        freq = store.get_preference("sync_frequency")
        self.frequency_spin.setValue(int(freq) if freq else 10)
        layout.addWidget(QtWidgets.QLabel("Sync frequency (minutes):"))
        layout.addWidget(self.frequency_spin)

        self.direction_combo = QtWidgets.QComboBox()
        self.direction_combo.addItems(["pull", "push", "bidirectional"])
        layout.addWidget(QtWidgets.QLabel("Default sync direction:"))
        layout.addWidget(self.direction_combo)

        self.conflict_combo = QtWidgets.QComboBox()
        self.conflict_combo.addItems(["remote_wins", "local_wins", "prompt"])
        layout.addWidget(QtWidgets.QLabel("Default conflict policy:"))
        layout.addWidget(self.conflict_combo)

        button_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def save(self) -> None:
        self.store.set_preference("sync_frequency", str(self.frequency_spin.value()))
        self.store.set_preference("default_direction", self.direction_combo.currentText())
        self.store.set_preference("default_conflict", self.conflict_combo.currentText())
