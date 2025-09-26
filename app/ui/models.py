"""Qt models for OneDrive folder tree."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from PySide6 import QtCore


@dataclass
class FolderNode:
    id: str
    name: str
    drive_id: Optional[str]
    is_folder: bool
    parent: Optional[FolderNode] = None
    children: List[FolderNode] = field(default_factory=list)
    fetched: bool = False
    is_loading: bool = False

    def append_child(self, node: FolderNode) -> None:
        node.parent = self
        self.children.append(node)

    def child(self, index: int) -> Optional[FolderNode]:
        if 0 <= index < len(self.children):
            return self.children[index]
        return None

    def row(self) -> int:
        if self.parent:
            return self.parent.children.index(self)
        return 0


class FolderTreeModel(QtCore.QAbstractItemModel):
    def __init__(self, root: FolderNode) -> None:
        super().__init__()
        self._root = root

    def columnCount(self, parent: QtCore.QModelIndex = QtCore.QModelIndex()) -> int:  # noqa: N802
        return 1

    def rowCount(self, parent: QtCore.QModelIndex = QtCore.QModelIndex()) -> int:  # noqa: N802
        node = self._node_for_index(parent)
        return len(node.children)

    def index(self, row: int, column: int, parent: QtCore.QModelIndex = QtCore.QModelIndex()) -> QtCore.QModelIndex:  # noqa: N802
        if not self.hasIndex(row, column, parent):
            return QtCore.QModelIndex()
        parent_node = self._node_for_index(parent)
        child_node = parent_node.child(row)
        if child_node is None:
            return QtCore.QModelIndex()
        return self.createIndex(row, column, child_node)

    def parent(self, index: QtCore.QModelIndex) -> QtCore.QModelIndex:  # noqa: N802
        if not index.isValid():
            return QtCore.QModelIndex()
        node = index.internalPointer()
        parent = node.parent
        if parent is None or parent is self._root:
            return QtCore.QModelIndex()
        return self.createIndex(parent.row(), 0, parent)

    def data(self, index: QtCore.QModelIndex, role: int = QtCore.Qt.DisplayRole):  # noqa: N802
        if not index.isValid():
            return None
        node: FolderNode = index.internalPointer()
        if role == QtCore.Qt.DisplayRole:
            return node.name
        return None

    def set_root_children(self, children: List[FolderNode]) -> None:
        self.beginResetModel()
        self._root.children = []
        for child in children:
            child.parent = self._root
            self._root.children.append(child)
        self.endResetModel()

    def insert_children(self, parent: FolderNode, children: List[FolderNode]) -> None:
        if not children:
            return
        parent_index = self.index_for_node(parent)
        start = len(parent.children)
        end = start + len(children) - 1
        self.beginInsertRows(parent_index, start, end)
        for child in children:
            child.parent = parent
            parent.children.append(child)
        self.endInsertRows()

    def index_for_node(self, node: FolderNode) -> QtCore.QModelIndex:
        if node is self._root:
            return QtCore.QModelIndex()
        return self.createIndex(node.row(), 0, node)

    def _node_for_index(self, index: QtCore.QModelIndex) -> FolderNode:
        if index.isValid():
            return index.internalPointer()
        return self._root
