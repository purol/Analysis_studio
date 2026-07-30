from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .graphics import GraphScene
from .model import PropertySpec, WorkflowNode
from .registry import NODE_SPECS


class PropertyEditor(QScrollArea):
    property_changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setMinimumWidth(300)
        self.scene: GraphScene | None = None
        self.node: WorkflowNode | None = None
        self._container = QWidget()
        self._layout = QVBoxLayout(self._container)
        self.setWidget(self._container)
        self.show_empty()

    def clear_layout(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def show_empty(self) -> None:
        self.scene = None
        self.node = None
        self.clear_layout()
        label = QLabel("Select a block to edit its parameters.")
        label.setWordWrap(True)
        self._layout.addWidget(label)
        self._layout.addStretch(1)

    def show_node(self, scene: GraphScene, node_id: str) -> None:
        if not node_id:
            self.show_empty()
            return
        self.scene = scene
        self.node = scene.graph.node(node_id)
        spec = NODE_SPECS[self.node.type]
        self.clear_layout()

        heading = QLabel(f"<b>{spec.label}</b>")
        self._layout.addWidget(heading)
        form_widget = QWidget()
        form = QFormLayout(form_widget)

        title = QLineEdit(self.node.title)
        title.editingFinished.connect(lambda: self._set_title(title.text()))
        form.addRow("Block name", title)

        for prop in spec.properties:
            widget = self._make_editor(prop, self.node.properties.get(prop.name, prop.default))
            form.addRow(prop.label, widget)
            if prop.help:
                help_label = QLabel(prop.help)
                help_label.setWordWrap(True)
                help_label.setStyleSheet("color: #7f8b99; font-size: 10px;")
                form.addRow("", help_label)

        self._layout.addWidget(form_widget)
        self._layout.addStretch(1)

    def _set_title(self, value: str) -> None:
        if not self.node or not self.scene:
            return
        self.node.title = value
        self.scene.node_items[self.node.id].set_title(value)
        self.scene.graph_changed.emit()
        self.property_changed.emit()

    def _set_property(self, name: str, value: object) -> None:
        if not self.node or not self.scene:
            return
        self.node.properties[name] = value
        self.scene.graph_changed.emit()
        self.property_changed.emit()

    def _make_editor(self, prop: PropertySpec, value: object) -> QWidget:
        if prop.kind == "choice":
            widget = QComboBox()
            widget.addItems(prop.choices)
            widget.setCurrentText(str(value))
            widget.currentTextChanged.connect(
                lambda text, name=prop.name: self._set_property(name, text)
            )
            return widget
        if prop.kind == "int":
            widget = QSpinBox()
            widget.setRange(-1_000_000_000, 1_000_000_000)
            widget.setValue(int(value))
            widget.valueChanged.connect(
                lambda number, name=prop.name: self._set_property(name, number)
            )
            return widget
        if prop.kind == "float":
            widget = QDoubleSpinBox()
            widget.setRange(-1.0e12, 1.0e12)
            widget.setDecimals(8)
            widget.setValue(float(value))
            widget.valueChanged.connect(
                lambda number, name=prop.name: self._set_property(name, number)
            )
            return widget
        if prop.kind == "bool":
            widget = QCheckBox()
            widget.setChecked(bool(value))
            widget.toggled.connect(
                lambda checked, name=prop.name: self._set_property(name, checked)
            )
            return widget
        if prop.multiline:
            widget = QPlainTextEdit(str(value))
            widget.setMinimumHeight(88)
            widget.textChanged.connect(
                lambda name=prop.name, editor=widget: self._set_property(
                    name, editor.toPlainText()
                )
            )
            return widget

        widget = QLineEdit(str(value))
        widget.editingFinished.connect(
            lambda name=prop.name, editor=widget: self._set_property(name, editor.text())
        )
        return widget
