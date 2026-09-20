"""Ordered recipe rows, with explicit enable switches and a large editor."""
from copy import deepcopy

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget, QAbstractItemView,
    QStyledItemDelegate, QComboBox,
)


class RecipeCellDelegate(QStyledItemDelegate):
    def __init__(self, columns, parent):
        super().__init__(parent)
        self.columns = columns

    def createEditor(self, parent, option, index):
        prop = self.columns[index.column()]
        if prop.kind == "choice":
            editor = QComboBox(parent)
            editor.addItems(prop.choices)
            return editor
        return super().createEditor(parent, option, index)

    def setEditorData(self, editor, index):
        if isinstance(editor, QComboBox):
            editor.setCurrentText(str(index.data()))
        else:
            super().setEditorData(editor, index)

    def setModelData(self, editor, model, index):
        if isinstance(editor, QComboBox):
            model.setData(index, editor.currentText())
        else:
            super().setModelData(editor, model, index)


class RecipeTableEditor(QWidget):
    changed = Signal(list)

    def __init__(self, columns, rows, parent=None, expandable=True):
        super().__init__(parent)
        self.columns = columns
        self.rows = deepcopy(rows)
        self.setMinimumHeight(270 if expandable else 230)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, len(columns))
        self.table.setItemDelegate(RecipeCellDelegate(columns, self.table))
        self.table.setHorizontalHeaderLabels([p.label for p in columns])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setMinimumHeight(180)
        self.table.itemChanged.connect(self._edited)
        layout.addWidget(self.table)
        buttons = QHBoxLayout()
        for label, callback in (("Add", self.add_row), ("Duplicate", self.duplicate_row),
                                ("Remove", self.remove_row), ("↑", lambda: self.move_row(-1)),
                                ("↓", lambda: self.move_row(1))):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, fn=callback: fn())
            buttons.addWidget(button)
        layout.addLayout(buttons)
        for index in range(buttons.count()):
            buttons.itemAt(index).widget().setMinimumWidth(0)
            buttons.itemAt(index).widget().setStyleSheet("padding: 5px 3px;")
        if expandable:
            expand = QPushButton("Open large table editor…")
            expand.clicked.connect(self.open_large)
            layout.addWidget(expand)
        self._render()

    def _render(self, selected=0):
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.rows))
        for index, row in enumerate(self.rows):
            for col, prop in enumerate(self.columns):
                value = row.get(prop.name, prop.default)
                item = QTableWidgetItem()
                if prop.kind == "bool":
                    item.setFlags((item.flags() | Qt.ItemFlag.ItemIsUserCheckable) & ~Qt.ItemFlag.ItemIsEditable)
                    item.setCheckState(Qt.CheckState.Checked if value else Qt.CheckState.Unchecked)
                else:
                    item.setText(str(value))
                self.table.setItem(index, col, item)
        self.table.resizeColumnsToContents()
        self.table.blockSignals(False)
        if self.rows:
            self.table.selectRow(min(selected, len(self.rows) - 1))

    def _edited(self, item):
        prop = self.columns[item.column()]
        value = item.checkState() == Qt.CheckState.Checked if prop.kind == "bool" else item.text()
        self.rows[item.row()][prop.name] = value
        self.changed.emit(deepcopy(self.rows))

    def replace_rows(self, rows, selected=0):
        self.rows = deepcopy(rows)
        self._render(selected)
        self.changed.emit(deepcopy(self.rows))

    def add_row(self):
        self.replace_rows([*self.rows, {p.name: deepcopy(p.default) for p in self.columns}], len(self.rows))

    def duplicate_row(self):
        index = self.table.currentRow()
        if index >= 0:
            rows = deepcopy(self.rows)
            rows.insert(index + 1, deepcopy(rows[index]))
            self.replace_rows(rows, index + 1)

    def remove_row(self):
        index = self.table.currentRow()
        if index >= 0:
            self.replace_rows(self.rows[:index] + self.rows[index + 1:], index)

    def move_row(self, offset):
        index = self.table.currentRow()
        target = index + offset
        if index >= 0 and 0 <= target < len(self.rows):
            rows = deepcopy(self.rows)
            rows[index], rows[target] = rows[target], rows[index]
            self.replace_rows(rows, target)

    def open_large(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Edit analysis rows — executed from top to bottom")
        dialog.resize(1050, 550)
        layout = QVBoxLayout(dialog)
        editor = RecipeTableEditor(self.columns, self.rows, expandable=False)
        layout.addWidget(editor)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.replace_rows(editor.rows)
