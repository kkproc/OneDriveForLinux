"""Reusable dialogs for multi-account management."""

from __future__ import annotations

import json
from typing import Callable, Optional

from PySide6 import QtCore, QtWidgets


class AddAccountDialog(QtWidgets.QDialog):
    """Dialog to add a new Microsoft account to the application."""

    account_added = QtCore.Signal(dict)

    def __init__(
        self,
        parent: Optional[QtWidgets.QWidget] = None,
        *,
        device_flow_handler: Optional[Callable[[], tuple[dict, Callable[[], dict]]]] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Connect a OneDrive Account")
        self._device_flow_handler = device_flow_handler
        self._poller: Optional[Callable[[], dict]] = None

        layout = QtWidgets.QVBoxLayout(self)

        intro_label = QtWidgets.QLabel(
            "Generate a device login code and sign in to connect another account."
        )
        intro_label.setWordWrap(True)
        layout.addWidget(intro_label)

        self.instructions = QtWidgets.QPlainTextEdit()
        self.instructions.setReadOnly(True)
        self.instructions.setMinimumHeight(160)
        layout.addWidget(self.instructions)

        self.status_label = QtWidgets.QLabel("Ready")
        layout.addWidget(self.status_label)

        button_box = QtWidgets.QDialogButtonBox()
        self.generate_button = button_box.addButton("Generate Code", QtWidgets.QDialogButtonBox.ActionRole)
        self.poll_button = button_box.addButton("Poll Login", QtWidgets.QDialogButtonBox.ActionRole)
        button_box.addButton(QtWidgets.QDialogButtonBox.Cancel)
        layout.addWidget(button_box)

        self.generate_button.clicked.connect(self._handle_generate)
        self.poll_button.clicked.connect(self._handle_poll)
        button_box.rejected.connect(self.reject)

        if not self._device_flow_handler:
            self.generate_button.setEnabled(False)
            self.status_label.setText("Device flow handler unavailable.")
        self.poll_button.setEnabled(False)

    def _handle_generate(self) -> None:
        if not self._device_flow_handler:
            QtWidgets.QMessageBox.information(self, "Unavailable", "Device login not configured.")
            return
        try:
            flow, poller = self._device_flow_handler()
        except Exception as exc:  # noqa: BLE001
            QtWidgets.QMessageBox.critical(self, "Device Flow Error", str(exc))
            return

        self._poller = poller
        self.poll_button.setEnabled(True)

        verification_uri = flow.get("verification_uri")
        complete_uri = flow.get("verification_uri_complete")
        device_code = flow.get("user_code")
        instructions = {
            "verification_uri": verification_uri,
            "verification_uri_complete": complete_uri,
            "user_code": device_code,
        }
        content = json.dumps(instructions, indent=2)
        self.instructions.setPlainText(content)
        self.status_label.setText("Visit the verification URL and enter the code, then click Poll Login.")

    def _handle_poll(self) -> None:
        if not self._poller:
            return
        self.status_label.setText("Waiting for confirmation…")
        QtWidgets.QApplication.processEvents()
        try:
            token = self._poller()
        except Exception as exc:  # noqa: BLE001
            QtWidgets.QMessageBox.critical(self, "Login Error", str(exc))
            return

        if "error" in token:
            QtWidgets.QMessageBox.warning(
                self,
                "Login Failed",
                token.get("error_description") or token.get("error") or "Unknown error",
            )
            return

        self.status_label.setText("Login successful")
        self.account_added.emit(token)
        self.accept()

