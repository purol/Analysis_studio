from __future__ import annotations

from pathlib import Path
import traceback

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QFileDialog,
    QFormLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QSplitter,
    QSpinBox,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .codegen import generate_loader_cpp, generate_project
from .execution import HTCondorExporter, LSFExecutor, LocalExecutor
from .graphics import BlockPalette, GraphScene, GraphView
from .model import Graph, Project, PropertySpec, WorkflowNode, new_id
from .properties import PropertyEditor
from .validation import validate_project


class GraphEditor(QWidget):
    def __init__(self, graph: Graph, parent=None) -> None:
        super().__init__(parent)
        self.graph = graph
        self.scene = GraphScene(graph, self)
        self.view = GraphView(self.scene, self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)


class ExecutionWorker(QObject):
    log = Signal(str)
    finished = Signal()
    failed = Signal(str)

    def __init__(
        self,
        project: Project,
        backend: str,
        project_path: str | None,
    ) -> None:
        super().__init__()
        self.project = project
        self.backend = backend
        self.project_path = project_path

    @Slot()
    def run(self) -> None:
        try:
            if self.backend == "local":
                executor = LocalExecutor(self.log.emit)
            elif self.backend == "lsf":
                queue = str(self.project.backend_options.get("lsf_queue", "s"))
                executor = LSFExecutor(queue, self.log.emit)
            else:
                raise ValueError(f"Unsupported direct-run backend: {self.backend}")
            executor.run(self.project, self.project_path)
        except Exception:
            self.failed.emit(traceback.format_exc())
            return
        self.finished.emit()


class TextPreviewDialog(QDialog):
    def __init__(self, title: str, text: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(900, 700)
        layout = QVBoxLayout(self)
        editor = QPlainTextEdit(text)
        editor.setReadOnly(True)
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(editor)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class BackendSettingsDialog(QDialog):
    def __init__(self, options: dict[str, object], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Backend settings")
        layout = QVBoxLayout(self)
        form_widget = QWidget()
        form = QFormLayout(form_widget)

        self.local_workers = QSpinBox()
        self.local_workers.setRange(1, 1024)
        self.local_workers.setValue(int(options.get("local_workers", 4)))
        form.addRow("Local workers", self.local_workers)

        from PySide6.QtWidgets import QLineEdit

        self.lsf_queue = QLineEdit(str(options.get("lsf_queue", "s")))
        form.addRow("LSF queue", self.lsf_queue)
        self.condor_universe = QLineEdit(
            str(options.get("condor_universe", "vanilla"))
        )
        form.addRow("HTCondor universe", self.condor_universe)
        layout.addWidget(form_widget)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> dict[str, object]:
        return {
            "local_workers": self.local_workers.value(),
            "lsf_queue": self.lsf_queue.text().strip() or "s",
            "condor_universe": self.condor_universe.text().strip() or "vanilla",
        }


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Analysis Studio")
        self.resize(1500, 900)
        self.project = Project.empty()
        self.project_path: Path | None = None
        self.dirty = False
        self.editors: list[GraphEditor] = []
        self.graph_tabs: dict[str, GraphEditor] = {}
        self.worker_thread: QThread | None = None
        self.execution_worker: ExecutionWorker | None = None

        self.palette = BlockPalette()
        self.properties = PropertyEditor()
        self.properties.set_choice_provider(self._dynamic_choices)
        self.properties.set_project_directory(Path.cwd())
        self.tabs = QTabWidget()
        self.tabs.currentChanged.connect(self._active_tab_changed)

        splitter = QSplitter()
        splitter.addWidget(self.palette)
        splitter.addWidget(self.tabs)
        splitter.addWidget(self.properties)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([215, 950, 330])
        self.setCentralWidget(splitter)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(10_000)
        log_dock = QDockWidget("Execution log", self)
        log_dock.setWidget(self.log_view)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, log_dock)

        self._create_actions()
        self._create_menus()
        self._create_toolbar()
        self._rebuild_tabs()
        self.statusBar().showMessage("Drag blocks or a For Each Region from the palette onto the canvas.")

    # ------------------------------------------------------------------ UI
    def _create_actions(self) -> None:
        self.new_action = QAction("New", self)
        self.new_action.setShortcut(QKeySequence.StandardKey.New)
        self.new_action.triggered.connect(self.new_project)

        self.open_action = QAction("Open…", self)
        self.open_action.setShortcut(QKeySequence.StandardKey.Open)
        self.open_action.triggered.connect(self.open_project)

        self.save_action = QAction("Save", self)
        self.save_action.setShortcut(QKeySequence.StandardKey.Save)
        self.save_action.triggered.connect(self.save_project)

        self.save_as_action = QAction("Save As…", self)
        self.save_as_action.setShortcut(QKeySequence.StandardKey.SaveAs)
        self.save_as_action.triggered.connect(self.save_project_as)

        self.delete_action = QAction("Delete selected", self)
        self.delete_action.setShortcut(QKeySequence.StandardKey.Delete)
        self.delete_action.triggered.connect(self.delete_selected)

        self.add_loader_program_action = QAction("New Loader Program…", self)
        self.add_loader_program_action.triggered.connect(self.add_loader_program)

        self.preview_cpp_action = QAction("Preview Loader C++", self)
        self.preview_cpp_action.triggered.connect(self.preview_loader_cpp)

        self.generate_action = QAction("Generate Files…", self)
        self.generate_action.triggered.connect(self.generate_files)

        self.run_action = QAction("Run / Submit", self)
        self.run_action.triggered.connect(self.run_project)

        self.export_condor_action = QAction("Export HTCondor DAG…", self)
        self.export_condor_action.triggered.connect(self.export_condor)

        self.backend_settings_action = QAction("Backend Settings…", self)
        self.backend_settings_action.triggered.connect(self.edit_backend_settings)

        self.validate_action = QAction("Validate", self)
        self.validate_action.triggered.connect(self.validate_project)

    def _create_menus(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        file_menu.addActions(
            [self.new_action, self.open_action, self.save_action, self.save_as_action]
        )
        file_menu.addSeparator()
        file_menu.addAction(self.generate_action)

        edit_menu = self.menuBar().addMenu("&Edit")
        edit_menu.addAction(self.delete_action)

        workflow_menu = self.menuBar().addMenu("&Workflow")
        workflow_menu.addAction(self.add_loader_program_action)
        workflow_menu.addAction(self.validate_action)
        workflow_menu.addAction(self.preview_cpp_action)
        workflow_menu.addSeparator()
        workflow_menu.addAction(self.run_action)
        workflow_menu.addAction(self.export_condor_action)
        workflow_menu.addAction(self.backend_settings_action)

    def _create_toolbar(self) -> None:
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        toolbar.addActions(
            [self.new_action, self.open_action, self.save_action, self.delete_action]
        )
        toolbar.addSeparator()
        toolbar.addAction(self.add_loader_program_action)
        toolbar.addAction(self.validate_action)
        toolbar.addAction(self.generate_action)
        toolbar.addSeparator()
        toolbar.addWidget(QLabel(" Backend: "))
        self.backend_combo = QComboBox()
        self.backend_combo.addItem("Local", "local")
        self.backend_combo.addItem("LSF / bsub", "lsf")
        self.backend_combo.addItem("HTCondor DAG export", "htcondor")
        self.backend_combo.currentIndexChanged.connect(self._backend_changed)
        toolbar.addWidget(self.backend_combo)
        toolbar.addAction(self.backend_settings_action)
        toolbar.addAction(self.run_action)

    # --------------------------------------------------------- graph / tabs
    def _active_editor(self) -> GraphEditor | None:
        widget = self.tabs.currentWidget()
        return widget if isinstance(widget, GraphEditor) else None

    def _active_tab_changed(self, _index: int) -> None:
        editor = self._active_editor()
        if not editor:
            return
        self.palette.set_scope(editor.graph.scope)
        self.properties.show_empty()
        if editor.graph.id == self.project.workflow.id:
            kind = "main workflow"
        else:
            kind = "Loader program"
        self.statusBar().showMessage(f"Editing {kind}: {editor.graph.name}")

    def _wire_editor(self, editor: GraphEditor) -> None:
        editor.scene.node_selected.connect(
            lambda node_id, scene=editor.scene: self.properties.show_node(
                scene, node_id
            ) if node_id else None
        )
        editor.scene.region_selected.connect(
            lambda region_id, scene=editor.scene: self.properties.show_region(
                scene, region_id
            ) if region_id else None
        )
        editor.scene.node_added.connect(
            lambda node_id, graph=editor.graph: self._node_added(graph, node_id)
        )
        editor.scene.node_activated.connect(
            lambda node_id, graph=editor.graph: self._node_activated(graph, node_id)
        )
        editor.scene.graph_changed.connect(self._mark_dirty)
        editor.scene.status_message.connect(self.statusBar().showMessage)

    def _add_graph_tab(self, graph: Graph, label: str) -> GraphEditor:
        editor = GraphEditor(graph)
        self._wire_editor(editor)
        self.editors.append(editor)
        self.graph_tabs[graph.id] = editor
        self.tabs.addTab(editor, label)
        return editor

    def _rebuild_tabs(self) -> None:
        self.tabs.clear()
        self.editors.clear()
        self.graph_tabs.clear()
        self._add_graph_tab(self.project.workflow, "Workflow")
        for graph in self.project.loader_programs.values():
            self._add_graph_tab(graph, f"Loader: {graph.name}")
        self.backend_combo.setCurrentIndex(
            max(0, self.backend_combo.findData(self.project.backend))
        )
        self._active_tab_changed(self.tabs.currentIndex())

    def _select_graph_tab(self, graph_id: str) -> None:
        editor = self.graph_tabs.get(graph_id)
        if editor is None:
            return
        self.tabs.setCurrentWidget(editor)

    def _node_added(self, graph: Graph, node_id: str) -> None:
        node = graph.node(node_id)
        if node.type == "loader_execute":
            if str(node.properties.get("loader_program", "")) not in self.project.loader_programs:
                if len(self.project.loader_programs) == 1:
                    node.properties["loader_program"] = next(iter(self.project.loader_programs))
        editor = self.graph_tabs.get(graph.id)
        if editor and self.properties.node and self.properties.node.id == node_id:
            self.properties.show_node(editor.scene, node_id)

    def _node_activated(self, graph: Graph, node_id: str) -> None:
        node = graph.node(node_id)
        if node.type == "loader_execute":
            self._select_graph_tab(str(node.properties.get("loader_program", "")))

    def _dynamic_choices(
        self,
        prop: PropertySpec,
        node: WorkflowNode,
        scene: GraphScene,
    ) -> list[tuple[str, str]]:
        if prop.kind == "loader_program_ref":
            return [
                (graph.name, graph.id) for graph in self.project.loader_programs.values()
            ]
        return []

    # --------------------------------------------------------------- project
    def _mark_dirty(self) -> None:
        self.dirty = True
        if not self.windowTitle().endswith(" *"):
            self.setWindowTitle(self.windowTitle() + " *")

    def _mark_clean(self) -> None:
        self.dirty = False
        name = self.project_path.name if self.project_path else self.project.name
        self.setWindowTitle(f"Analysis Studio — {name}")

    def _backend_changed(self) -> None:
        backend = str(self.backend_combo.currentData())
        if backend and backend != self.project.backend:
            self.project.backend = backend
            self._mark_dirty()
        self.run_action.setText("Export DAG" if backend == "htcondor" else "Run / Submit")

    def _confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        answer = QMessageBox.question(
            self,
            "Unsaved changes",
            "Discard the unsaved changes?",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Discard

    def new_project(self) -> None:
        if not self._confirm_discard():
            return
        name, ok = QInputDialog.getText(self, "New project", "Project name:")
        if not ok:
            return
        self.project = Project.empty(name.strip() or "Untitled analysis")
        self.project_path = None
        self.properties.set_project_directory(Path.cwd())
        self._rebuild_tabs()
        self._mark_clean()

    def open_project(self) -> None:
        if not self._confirm_discard():
            return
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Open Analysis Studio project",
            "",
            "Analysis Studio project (*.astudio.json *.bflow.json *.json)",
        )
        if not filename:
            return
        try:
            self.project = Project.load(filename)
        except Exception as exc:
            QMessageBox.critical(self, "Could not open project", str(exc))
            return
        self.project_path = Path(filename)
        self.properties.set_project_directory(self.project_path.parent)
        self._rebuild_tabs()
        self._mark_clean()

    def save_project(self) -> bool:
        if self.project_path is None:
            return self.save_project_as()
        try:
            self.project.save(self.project_path)
        except Exception as exc:
            QMessageBox.critical(self, "Could not save project", str(exc))
            return False
        self._mark_clean()
        self.statusBar().showMessage(f"Saved {self.project_path}", 5000)
        return True

    def save_project_as(self) -> bool:
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Save Analysis Studio project",
            f"{self.project.name}.astudio.json",
            "Analysis Studio project (*.astudio.json)",
        )
        if not filename:
            return False
        if not filename.endswith(".astudio.json"):
            filename += ".astudio.json"
        self.project_path = Path(filename)
        self.properties.set_project_directory(self.project_path.parent)
        return self.save_project()

    def delete_selected(self) -> None:
        editor = self._active_editor()
        if editor:
            editor.scene.delete_selected()

    def add_loader_program(self) -> None:
        name, ok = QInputDialog.getText(
            self, "New Loader program", "Program name:"
        )
        if not ok or not name.strip():
            return
        graph_id = "loader_program_" + new_id("graph").split("_", 1)[1]
        graph = Graph(id=graph_id, name=name.strip(), scope="loader")
        self.project.loader_programs[graph.id] = graph
        editor = self._add_graph_tab(graph, f"Loader: {graph.name}")
        self.tabs.setCurrentWidget(editor)
        self._mark_dirty()

    # --------------------------------------------------------- generation/run
    def preview_loader_cpp(self) -> None:
        editor = self._active_editor()
        if not editor or editor.graph.scope != "loader":
            QMessageBox.information(
                self, "Loader program", "Select a Loader program tab first."
            )
            return
        try:
            code = generate_loader_cpp(editor.graph)
        except Exception as exc:
            QMessageBox.critical(self, "Could not generate C++", str(exc))
            return
        TextPreviewDialog(f"C++ preview — {editor.graph.name}", code, self).exec()

    def _validation_errors(self) -> list[str]:
        return validate_project(self.project)

    def validate_project(self) -> bool:
        errors = self._validation_errors()
        if errors:
            QMessageBox.warning(
                self,
                "Validation problems",
                "\n".join(f"• {error}" for error in errors),
            )
            return False
        QMessageBox.information(self, "Validation", "The project is valid.")
        return True

    def generate_files(self) -> None:
        if self._validation_errors():
            self.validate_project()
            return
        directory = QFileDialog.getExistingDirectory(self, "Generated files directory")
        if not directory:
            return
        try:
            paths = generate_project(self.project, directory)
        except Exception as exc:
            QMessageBox.critical(self, "Generation failed", str(exc))
            return
        self.log_view.appendPlainText(
            "Generated:\n" + "\n".join(f"  {path}" for path in paths)
        )
        self.statusBar().showMessage(f"Generated {len(paths)} file(s).", 5000)

    def export_condor(self) -> None:
        if self._validation_errors():
            self.validate_project()
            return
        directory = QFileDialog.getExistingDirectory(self, "HTCondor DAG directory")
        if not directory:
            return
        try:
            HTCondorExporter(self.log_view.appendPlainText).export(
                self.project,
                directory,
                str(self.project_path) if self.project_path else None,
            )
        except Exception as exc:
            QMessageBox.critical(self, "HTCondor export failed", str(exc))
            return
        QMessageBox.information(
            self,
            "HTCondor DAG",
            f"Exported to {directory}.\nRun: condor_submit_dag workflow.dag",
        )

    def edit_backend_settings(self) -> None:
        dialog = BackendSettingsDialog(self.project.backend_options, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.project.backend_options.update(dialog.values())
        self._mark_dirty()

    def run_project(self) -> None:
        backend = str(self.backend_combo.currentData())
        if backend == "htcondor":
            self.export_condor()
            return
        if self.worker_thread is not None:
            QMessageBox.information(self, "Execution", "A workflow is already active.")
            return
        if self._validation_errors():
            self.validate_project()
            return
        if backend == "lsf":
            answer = QMessageBox.question(
                self,
                "Submit LSF jobs",
                "Submit the expanded workflow to LSF/bsub now?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        self.log_view.appendPlainText(f"\n=== Starting {backend} workflow ===")
        self.run_action.setEnabled(False)
        thread = QThread(self)
        worker = ExecutionWorker(
            Project.from_dict(self.project.to_dict()),
            backend,
            str(self.project_path) if self.project_path else None,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.log.connect(self.log_view.appendPlainText)
        worker.finished.connect(self._execution_success)
        worker.failed.connect(self._execution_failure)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._thread_finished)
        thread.finished.connect(thread.deleteLater)
        self.worker_thread = thread
        self.execution_worker = worker
        thread.start()

    @Slot()
    def _execution_success(self) -> None:
        self._execution_done(True, "Workflow completed or submitted.")

    @Slot(str)
    def _execution_failure(self, message: str) -> None:
        self._execution_done(False, message)

    def _execution_done(self, success: bool, message: str) -> None:
        self.log_view.appendPlainText(message)
        if not success:
            QMessageBox.critical(self, "Workflow failed", message)
        else:
            self.statusBar().showMessage(message, 5000)

    def _thread_finished(self) -> None:
        self.run_action.setEnabled(True)
        self.worker_thread = None
        self.execution_worker = None

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self.worker_thread is not None:
            QMessageBox.warning(
                self,
                "Workflow active",
                "Wait for the active workflow to finish before closing.",
            )
            event.ignore()
            return
        if self._confirm_discard():
            event.accept()
        else:
            event.ignore()
