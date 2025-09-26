"""Notification service for sync events."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, List

logger = logging.getLogger(__name__)

try:  # pragma: no cover
    from gi.repository import Notify
except Exception:  # pragma: no cover
    Notify = None  # type: ignore[assignment]

try:  # pragma: no cover
    from PySide6 import QtCore
except Exception:  # pragma: no cover
    QtCore = None  # type: ignore[assignment]


@dataclass(slots=True)
class Notification:
    title: str
    message: str
    urgency: str = "normal"


class Notifier:
    def __init__(self) -> None:
        self._subscribers: List[Callable[[Notification], None]] = []
        if Notify:
            try:
                Notify.init("OneDrive Linux Sync")
            except Exception:  # pragma: no cover
                logger.warning("Failed to initialize libnotify")

    def subscribe(self, handler: Callable[[Notification], None]) -> None:
        self._subscribers.append(handler)

    def dispatch(self, event: Notification) -> None:
        logger.debug("Dispatching notification: %s", event)
        for handler in self._subscribers:
            try:
                handler(event)
            except Exception:  # pragma: no cover
                logger.exception("Notification handler error")
        self._send_desktop_notification(event)

    def _send_desktop_notification(self, event: Notification) -> None:
        if not Notify:
            return
        try:
            urgency = Notify.Urgency.NORMAL
            if event.urgency == "critical":
                urgency = Notify.Urgency.CRITICAL
            elif event.urgency == "low":
                urgency = Notify.Urgency.LOW
            notification = Notify.Notification.new(event.title, event.message)
            notification.set_urgency(urgency)
            notification.show()
        except Exception:  # pragma: no cover
            logger.exception("Failed to show desktop notification")


if QtCore:

    class QtNotifier(QtCore.QObject):  # pragma: no cover
        notification = QtCore.Signal(object)

        def __init__(self) -> None:
            super().__init__()

        def dispatch(self, event: Notification) -> None:
            self.notification.emit(event)

    def connect_qt_notifier(notifier: Notifier, qt_notifier: QtNotifier) -> None:
        notifier.subscribe(qt_notifier.dispatch)

else:  # pragma: no cover

    class QtNotifier:
        def dispatch(self, event: Notification) -> None:
            logger.debug("Qt notifier noop: %s", event)

    def connect_qt_notifier(notifier: Notifier, qt_notifier: QtNotifier) -> None:
        notifier.subscribe(qt_notifier.dispatch)

