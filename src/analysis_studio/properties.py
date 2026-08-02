from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .foreach_tokens import source_fields, token_bindings, token_definitions
from .graphics import GraphScene
from .model import (
    ForEachRegion,
    Project,
    PropertySpec,
    WorkflowNode,
    custom_command_output_name,
    safe_program_name,
)
from .registry import (
    FOREACH_COMMON_PROPERTIES,
    FOREACH_SOURCE_PROPERTIES,
    FOREACH_TRAILING_PROPERTIES,
    NODE_SPECS,
    node_property_visible,
)


ChoiceProvider = Callable[
    [PropertySpec, WorkflowNode, GraphScene], list[tuple[str, str]]
]


class TokenBindingsEditor(QWidget):
    """Explicit For Each variable definitions with source and preview."""

    changed = Signal(list)

    def __init__(
        self,
        region: ForEachRegion,
        project_directory: Path,
        inherited_context: dict[str, object] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.region = region
        self.project_directory = project_directory
        self.inherited_context = inherited_context or {}
        self._updating = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        intro = QLabel(
            "Define only the variables that the loop body needs. There are no "
            "hidden variables. A block inside this region may use the shown "
            "expression, for example {input_file}."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #8fb6d4;")
        layout.addWidget(intro)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["Variable name", "Value from source", "Preview", ""]
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(170)
        layout.addWidget(self.table)

        buttons = QHBoxLayout()
        add_button = QPushButton("Add variable")
        add_button.clicked.connect(lambda _checked=False: self.add_empty_row())
        refresh_button = QPushButton("Refresh source / previews")
        refresh_button.clicked.connect(lambda _checked=False: self.refresh())
        buttons.addWidget(add_button)
        buttons.addWidget(refresh_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.refresh()

    def _fields(self):
        return source_fields(
            self.region.properties,
            self.project_directory,
            self.inherited_context,
        )

    def refresh(self) -> None:
        bindings = self._current_bindings() if self.table.rowCount() else token_bindings(
            self.region.properties
        )
        self._updating = True
        try:
            self.table.setRowCount(0)
            for binding in bindings:
                self._append_row(binding.get("name", ""), binding.get("source", ""))
        finally:
            self._updating = False

    def add_empty_row(self) -> None:
        fields = self._fields()
        source = fields[0].key if fields else ""
        existing = {item["name"] for item in self._current_bindings()}
        index = 1
        name = "variable"
        while name in existing:
            index += 1
            name = f"variable_{index}"
        self._append_row(name, source)
        self._commit()

    def _append_row(self, name: str, source: str) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)

        name_editor = QLineEdit(name)
        name_editor.setPlaceholderText("input_file")
        name_editor.textChanged.connect(lambda _text: self._commit())
        self.table.setCellWidget(row, 0, name_editor)

        source_combo = QComboBox()
        fields = self._fields()
        for field in fields:
            source_combo.addItem(field.label, field.key)
        source_index = source_combo.findData(source)
        source_combo.setCurrentIndex(source_index if source_index >= 0 else -1)
        source_combo.currentIndexChanged.connect(
            lambda _index, combo=source_combo: self._source_changed(combo)
        )
        self.table.setCellWidget(row, 1, source_combo)

        preview = QTableWidgetItem("")
        preview.setFlags(preview.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(row, 2, preview)
        self._update_preview(row)

        remove_button = QPushButton("Remove")
        remove_button.clicked.connect(
            lambda _checked=False, button=remove_button: self._remove_button_row(button)
        )
        self.table.setCellWidget(row, 3, remove_button)

    def _source_changed(self, combo: QComboBox) -> None:
        for row in range(self.table.rowCount()):
            if self.table.cellWidget(row, 1) is combo:
                self._update_preview(row)
                break
        self._commit()

    def _update_preview(self, row: int) -> None:
        combo = self.table.cellWidget(row, 1)
        item = self.table.item(row, 2)
        if not isinstance(combo, QComboBox) or item is None:
            return
        previews = {field.key: field.preview for field in self._fields()}
        item.setText(previews.get(str(combo.currentData() or ""), "<unavailable>"))

    def _remove_button_row(self, button: QPushButton) -> None:
        for row in range(self.table.rowCount()):
            if self.table.cellWidget(row, 3) is button:
                self.table.removeRow(row)
                self._commit()
                return

    def _current_bindings(self) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        for row in range(self.table.rowCount()):
            name_widget = self.table.cellWidget(row, 0)
            source_widget = self.table.cellWidget(row, 1)
            if not isinstance(name_widget, QLineEdit) or not isinstance(
                source_widget, QComboBox
            ):
                continue
            result.append(
                {
                    "name": name_widget.text().strip(),
                    "source": str(source_widget.currentData() or ""),
                }
            )
        return result

    def _commit(self) -> None:
        if self._updating:
            return
        self.changed.emit(self._current_bindings())


class PropertyEditor(QScrollArea):
    property_changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setMinimumWidth(360)
        self.scene: GraphScene | None = None
        self.node: WorkflowNode | None = None
        self.region: ForEachRegion | None = None
        self.choice_provider: ChoiceProvider | None = None
        self.project: Project | None = None
        self.backend = "local"
        self.project_directory = Path.cwd()
        self._container = QWidget()
        self._layout = QVBoxLayout(self._container)
        self.setWidget(self._container)
        self.show_empty()

    def set_choice_provider(self, provider: ChoiceProvider) -> None:
        self.choice_provider = provider

    def set_project(self, project: Project) -> None:
        self.project = project

    def set_backend(self, backend: str) -> None:
        changed = backend != self.backend
        self.backend = backend
        if changed and self.scene is not None:
            if self.node is not None:
                self.show_node(self.scene, self.node.id)
            elif self.region is not None:
                self.show_region(self.scene, self.region.id)

    def set_project_directory(self, directory: str | Path | None) -> None:
        self.project_directory = Path(directory or Path.cwd()).resolve()

    def clear_layout(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def show_empty(self) -> None:
        self.scene = None
        self.node = None
        self.region = None
        self.clear_layout()
        label = QLabel("Select a block or For Each region to edit its parameters.")
        label.setWordWrap(True)
        self._layout.addWidget(label)
        self._layout.addStretch(1)

    def _regions_for_node(
        self, scene: GraphScene, node_id: str
    ) -> list[ForEachRegion]:
        try:
            return scene.graph.regions_for_node(node_id)
        except ValueError:
            return []

    def _preview_context(
        self, regions: list[ForEachRegion]
    ) -> dict[str, object]:
        context: dict[str, object] = {}
        for region in regions:
            definitions = token_definitions(
                region.properties,
                self.project_directory,
                context,
            )
            for definition in definitions:
                if definition.name:
                    context[definition.name] = definition.preview
        return context

    def _show_available_variables(
        self, regions: list[ForEachRegion], heading_text: str = "Available For Each variables"
    ) -> None:
        heading = QLabel(f"<b>{heading_text}</b>")
        self._layout.addWidget(heading)
        inherited_context: dict[str, object] = {}
        total = 0
        for region in regions:
            definitions = token_definitions(
                region.properties,
                self.project_directory,
                inherited_context,
            )
            if definitions:
                region_label = QLabel(f"<b>{region.title}</b>")
                region_label.setStyleSheet("color: #8fb6d4;")
                self._layout.addWidget(region_label)
            for definition in definitions:
                total += 1
                row = QWidget()
                layout = QHBoxLayout(row)
                layout.setContentsMargins(0, 0, 0, 0)
                label = QLabel(
                    f"<code>{definition.expression}</code> = "
                    f"{definition.source_label}<br><span style='color:#7f8b99'>"
                    f"Preview: {definition.preview}</span>"
                )
                label.setWordWrap(True)
                copy_button = QPushButton("Copy")
                copy_button.setMaximumWidth(58)
                copy_button.clicked.connect(
                    lambda _checked=False, text=definition.expression: QApplication.clipboard().setText(text)
                )
                layout.addWidget(label, 1)
                layout.addWidget(copy_button)
                self._layout.addWidget(row)
                if definition.name:
                    inherited_context[definition.name] = definition.preview
        if total == 0:
            note = QLabel(
                "No variables are defined. Select a surrounding For Each border "
                "and add only the variables that its loop body should use."
            )
            note.setWordWrap(True)
            note.setStyleSheet("color: #c9b55f;")
            self._layout.addWidget(note)

    def _node_property_visible(self, prop: PropertySpec) -> bool:
        if self.node is None:
            return False
        return node_property_visible(
            self.node.type, prop.name, self.backend, self.node.properties
        )

    def show_node(self, scene: GraphScene, node_id: str) -> None:
        if not node_id:
            self.show_empty()
            return
        self.scene = scene
        self.node = scene.graph.node(node_id)
        self.region = None
        spec = NODE_SPECS[self.node.type]
        self.clear_layout()

        heading = QLabel(f"<b>{spec.label}</b>")
        self._layout.addWidget(heading)
        if scene.graph.scope == "workflow":
            containing_regions = self._regions_for_node(scene, node_id)
            if containing_regions:
                self._show_available_variables(containing_regions)

        if self.node.type == "loader_execute":
            program_id = str(self.node.properties.get("loader_program", ""))
            program_name = ""
            program_property = next(
                (prop for prop in spec.properties if prop.name == "loader_program"),
                None,
            )
            if program_property and self.choice_provider:
                for label, value in self.choice_provider(
                    program_property, self.node, scene
                ):
                    if str(value) == program_id:
                        program_name = label
                        break
            if program_name:
                executable_note = QLabel(
                    "Executable is derived from the Loader program name: "
                    f"<b>./bin/{safe_program_name(program_name)}</b>"
                )
            else:
                executable_note = QLabel(
                    "Select a Loader program. Its executable path is generated "
                    "automatically as <b>./bin/&lt;program_name&gt;</b>."
                )
            executable_note.setWordWrap(True)
            executable_note.setStyleSheet("color: #8fb6d4;")
            self._layout.addWidget(executable_note)
        elif self.node.type == "custom_command":
            artifact_note = QLabel(
                "Compile builds or copies this project-local source "
                f"as <b>./bin/{custom_command_output_name(self.node, self.project)}</b>. Runtime "
                "For Each values belong in argv or runtime path properties, not in "
                "the Code / script path."
            )
            artifact_note.setWordWrap(True)
            artifact_note.setStyleSheet("color: #8fb6d4;")
            self._layout.addWidget(artifact_note)

        roots = scene.start_roots()
        if any(root.id == self.node.id for root in roots):
            start_index = next(
                (
                    index
                    for index, root in enumerate(roots, start=1)
                    if root.id == self.node.id
                ),
                None,
            )
            if start_index is not None:
                note = QLabel(
                    f"Start order: <b>{start_index}</b> — right-click the block "
                    "on the canvas to change it."
                )
                note.setWordWrap(True)
                note.setStyleSheet("color: #c9b55f;")
                self._layout.addWidget(note)

        form_widget = QWidget()
        form = QFormLayout(form_widget)
        title = QLineEdit(self.node.title)
        title.editingFinished.connect(lambda: self._set_node_title(title.text()))
        form.addRow("Block name", title)

        for prop in spec.properties:
            if not self._node_property_visible(prop):
                continue
            widget = self._make_node_editor(
                prop, self.node.properties.get(prop.name, prop.default)
            )
            form.addRow(prop.label, widget)
            if prop.help:
                form.addRow("", self._help_label(prop.help))

        self._layout.addWidget(form_widget)
        self._layout.addStretch(1)

    def show_region(self, scene: GraphScene, region_id: str) -> None:
        if not region_id:
            self.show_empty()
            return
        self.scene = scene
        self.node = None
        self.region = scene.graph.region(region_id)
        self.clear_layout()

        heading = QLabel("<b>For Each Region</b>")
        self._layout.addWidget(heading)
        note = QLabel(
            "Every workflow block whose center is inside this border belongs to "
            "the loop. A fully contained For Each border becomes a nested loop. "
            "Nested sources and blocks may use variables from outer regions."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #8fb6d4;")
        self._layout.addWidget(note)

        ancestors = scene.graph.ancestor_regions(self.region.id)
        if ancestors:
            chain = " → ".join(item.title for item in [*ancestors, self.region])
            nesting = QLabel(f"Nesting: <b>{chain}</b>")
            nesting.setWordWrap(True)
            nesting.setStyleSheet("color: #c9b55f;")
            self._layout.addWidget(nesting)
            self._show_available_variables(
                ancestors,
                "Variables inherited from outer For Each regions",
            )
        inherited_context = self._preview_context(ancestors)

        form_widget = QWidget()
        form = QFormLayout(form_widget)
        title = QLineEdit(self.region.title)
        title.editingFinished.connect(lambda: self._set_region_title(title.text()))
        form.addRow("Region name", title)

        source_prop = FOREACH_COMMON_PROPERTIES[0]
        source_widget = self._make_region_editor(
            source_prop,
            self.region.properties.get(source_prop.name, source_prop.default),
        )
        form.addRow(source_prop.label, source_widget)

        mode = str(self.region.properties.get("source_mode", "root_files"))
        for prop in FOREACH_SOURCE_PROPERTIES.get(mode, ()):
            widget = self._make_region_editor(
                prop, self.region.properties.get(prop.name, prop.default)
            )
            form.addRow(prop.label, widget)
            if prop.help:
                form.addRow("", self._help_label(prop.help))

        member_label = QLabel(str(len(self.region.member_node_ids)))
        form.addRow("Directly contained blocks", member_label)
        nested_label = QLabel(str(len(scene.graph.child_regions(self.region.id))))
        form.addRow("Nested For Each regions", nested_label)
        self._layout.addWidget(form_widget)

        variables_heading = QLabel("<b>Loop variables</b>")
        self._layout.addWidget(variables_heading)
        binding_editor = TokenBindingsEditor(
            self.region,
            self.project_directory,
            inherited_context,
        )
        binding_editor.changed.connect(
            lambda bindings: self._set_region_property("tokens", bindings)
        )
        self._layout.addWidget(binding_editor)

        if FOREACH_TRAILING_PROPERTIES:
            trailing_widget = QWidget()
            trailing_form = QFormLayout(trailing_widget)
            for prop in FOREACH_TRAILING_PROPERTIES:
                widget = self._make_region_editor(
                    prop, self.region.properties.get(prop.name, prop.default)
                )
                trailing_form.addRow(prop.label, widget)
                if prop.help:
                    trailing_form.addRow("", self._help_label(prop.help))
            self._layout.addWidget(trailing_widget)
        self._layout.addStretch(1)

    def _help_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet("color: #7f8b99; font-size: 10px;")
        return label

    def _set_node_title(self, value: str) -> None:
        if not self.node or not self.scene:
            return
        self.node.title = value.strip() or NODE_SPECS[self.node.type].label
        self.scene.node_items[self.node.id].set_title(self.node.title)
        self.scene.graph_changed.emit()
        self.property_changed.emit()

    def _set_region_title(self, value: str) -> None:
        if not self.region or not self.scene:
            return
        self.region.title = value.strip() or "For Each"
        self.scene.region_items[self.region.id].set_title(self.region.title)
        self.scene.graph_changed.emit()
        self.property_changed.emit()

    def _set_node_property(self, name: str, value: object) -> None:
        if not self.node or not self.scene:
            return
        self.node.properties[name] = value
        self.scene.refresh_start_badges()
        self.scene.graph_changed.emit()
        self.property_changed.emit()
        refresh_panel = name in {"loader_program", "build_mode"} or (
            self.node.type == "custom_command" and name == "code"
        )
        if refresh_panel:
            scene = self.scene
            node_id = self.node.id
            QTimer.singleShot(0, lambda: self.show_node(scene, node_id))

    def _set_region_property(self, name: str, value: object) -> None:
        if not self.region or not self.scene:
            return
        region_id = self.region.id
        scene = self.scene
        self.region.properties[name] = value
        self.scene.region_items[self.region.id].refresh_label()
        self.scene.graph_changed.emit()
        self.property_changed.emit()
        if name == "source_mode":
            QTimer.singleShot(0, lambda: self.show_region(scene, region_id))

    def _make_node_editor(self, prop: PropertySpec, value: object) -> QWidget:
        return self._make_editor(prop, value, self._set_node_property, dynamic=True)

    def _make_region_editor(self, prop: PropertySpec, value: object) -> QWidget:
        return self._make_editor(prop, value, self._set_region_property, dynamic=False)

    def _make_editor(
        self,
        prop: PropertySpec,
        value: object,
        setter,
        dynamic: bool,
    ) -> QWidget:
        if prop.kind == "choice":
            widget = QComboBox()
            widget.addItems(prop.choices)
            widget.setCurrentText(str(value))
            widget.currentTextChanged.connect(
                lambda text, name=prop.name: setter(name, text)
            )
            return widget

        if dynamic and prop.kind == "loader_program_ref":
            widget = QComboBox()
            widget.addItem("<select>", "")
            entries = (
                self.choice_provider(prop, self.node, self.scene)
                if self.choice_provider and self.node and self.scene
                else []
            )
            for label, data in entries:
                widget.addItem(label, data)
            index = widget.findData(str(value))
            widget.setCurrentIndex(index if index >= 0 else 0)
            widget.currentIndexChanged.connect(
                lambda _index, name=prop.name, combo=widget: setter(
                    name, str(combo.currentData() or "")
                )
            )
            return widget

        if prop.kind == "int":
            widget = QSpinBox()
            widget.setRange(-1_000_000_000, 1_000_000_000)
            widget.setValue(int(value))
            widget.valueChanged.connect(
                lambda number, name=prop.name: setter(name, number)
            )
            return widget
        if prop.kind == "float":
            widget = QDoubleSpinBox()
            widget.setRange(-1.0e12, 1.0e12)
            widget.setDecimals(8)
            widget.setValue(float(value))
            widget.valueChanged.connect(
                lambda number, name=prop.name: setter(name, number)
            )
            return widget
        if prop.kind == "bool":
            widget = QCheckBox()
            widget.setChecked(bool(value))
            widget.toggled.connect(
                lambda checked, name=prop.name: setter(name, checked)
            )
            return widget

        if prop.multiline or prop.kind in {"argv", "node_refs"}:
            widget = QPlainTextEdit(str(value))
            compact_multiline = {
                "argv",
                "compile_command",
                "additional_sources",
                "compile_flags",
                "link_flags",
            }
            if prop.name in compact_multiline:
                widget.setFixedHeight(70)
            else:
                widget.setMinimumHeight(100)
            widget.textChanged.connect(
                lambda name=prop.name, editor=widget: setter(
                    name, editor.toPlainText()
                )
            )
            return widget

        widget = QLineEdit(str(value))
        widget.editingFinished.connect(
            lambda name=prop.name, editor=widget: setter(name, editor.text())
        )
        return widget
