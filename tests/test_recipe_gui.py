import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QGroupBox, QLineEdit, QPushButton

from analysis_studio.graphics import GraphScene
from analysis_studio.model import Graph
from analysis_studio.properties import PropertyEditor
from analysis_studio.recipes import CUT_COLUMNS
from analysis_studio.registry import NODE_SPECS
from analysis_studio.table_editor import RecipeTableEditor


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_table_edits_reorder_disable_duplicate_and_remove(app):
    editor = RecipeTableEditor(CUT_COLUMNS, [dict(name="A", condition="a"), dict(name="B", condition="b")])
    changes = []
    editor.changed.connect(changes.append)
    editor.table.selectRow(0)
    editor.move_row(1)
    assert [r["name"] for r in changes[-1]] == ["B", "A"]
    editor.table.item(1, 0).setCheckState(Qt.CheckState.Unchecked)
    assert changes[-1][1]["enabled"] is False
    editor.duplicate_row()
    assert len(changes[-1]) == 3
    editor.table.item(2, 2).setText("edited")
    assert changes[-1][1]["condition"] == "a"
    editor.remove_row()
    assert len(changes[-1]) == 2
    editor.close()


def test_property_panel_hides_cpp_settings_and_updates_recipe(app):
    graph = Graph("analysis", "Analysis", "loader")
    declaration = graph.add_node(NODE_SPECS["loader_decl"], 0, 0)
    flow = graph.add_node(NODE_SPECS["cut_flow"], 250, 0)
    fit = graph.add_node(NODE_SPECS["fit"], 500, 0)
    scene = GraphScene(graph)
    panel = PropertyEditor()
    panel.resize(380, 600)
    panel.show()
    panel.show_node(scene, declaration.id)
    app.processEvents()
    group = panel.findChild(QGroupBox)
    assert not group.isChecked()
    assert all(not field.isVisible() for field in group.findChildren(QLineEdit))
    group.setChecked(True)
    assert all(field.isVisible() for field in group.findChildren(QLineEdit))
    panel.show_node(scene, flow.id)
    app.processEvents()
    table = panel.findChild(RecipeTableEditor)
    table.table.item(0, 2).setText("M > 1.71")
    assert flow.properties["steps"][0]["condition"] == "M > 1.71"
    panel.show_node(scene, fit.id)
    app.processEvents()
    fit.properties["model"] = "RooBifurGauss"
    button = next(b for b in panel.findChildren(QPushButton) if b.text().startswith("Replace parameters"))
    button.click()
    assert len(fit.properties["parameters"]) == 3
    app.processEvents()
    table = panel.findChild(RecipeTableEditor)
    assert table.geometry().bottom() < button.geometry().top()
    assert panel.verticalScrollBar().maximum() > 0
    panel.close()


def test_all_analysis_module_forms_and_choice_cells(app):
    from analysis_studio.analysis_modules import MODULE_SPECS
    from PySide6.QtWidgets import QComboBox, QStyleOptionViewItem
    graph = Graph("modules", "Modules", "loader")
    nodes = [graph.add_node(spec, 0, 0) for spec in MODULE_SPECS.values()]
    scene = GraphScene(graph)
    panel = PropertyEditor()
    panel.resize(380, 600)
    panel.show()
    for node in nodes:
        panel.show_node(scene, node.id)
        app.processEvents()
        assert panel.node.id == node.id
    variables = next(n for n in nodes if n.type == "variables")
    panel.show_node(scene, variables.id)
    app.processEvents()
    table = panel.findChild(RecipeTableEditor).table
    index = table.model().index(0, 2)
    delegate = table.itemDelegate()
    editor = delegate.createEditor(table, QStyleOptionViewItem(), index)
    assert isinstance(editor, QComboBox)
    editor.setCurrentText("GetAverage")
    delegate.setModelData(editor, table.model(), index)
    assert variables.properties["variables"][0]["operation"] == "GetAverage"
    panel.close()


def test_advanced_toggle_is_display_only_for_multiple_declarations(app):
    from analysis_studio.codegen import generate_loader_cpp
    from analysis_studio.validation import validate_loader_graph
    graph = Graph("multi", "Multiple loaders", "loader")
    declarations = []
    for _ in range(3):
        decl = graph.add_node(NODE_SPECS["loader_decl"], 0, 0)
        end = graph.add_node(NODE_SPECS["loader_end"], 0, 200)
        graph.add_edge(decl.id, "out", end.id, "in")
        declarations.append(decl)
    declarations[1].properties["variable_name"] = "signal_loader"
    scene = GraphScene(graph)
    panel = PropertyEditor()
    before = generate_loader_cpp(graph)
    for decl in declarations:
        panel.show_node(scene, decl.id)
        app.processEvents()
        group = panel.findChild(QGroupBox)
        group.setChecked(True)
        group.setChecked(False)
    assert generate_loader_cpp(graph) == before
    assert {d.properties["variable_name"] for d in declarations} == {"loader", "signal_loader", "loader_3"}
    declarations[1].properties["variable_name"] = "loader"
    assert any("duplicated" in e for e in validate_loader_graph(graph))
    panel.close()


def test_fastbdt_dropdown_selection_commits_and_survives_row_move(app):
    from PySide6.QtWidgets import QComboBox
    from analysis_studio.analysis_modules import HYPERPARAMETERS
    graph = Graph("train", "Train", "loader")
    node = graph.add_node(NODE_SPECS["bdt_train"], 0, 0)
    node.properties["hyperparameters"] = [dict(name="NTrees", value=3)]
    scene = GraphScene(graph)
    panel = PropertyEditor()
    panel.show_node(scene, node.id)
    editor = panel.findChild(RecipeTableEditor)
    table = editor.table
    combo = table.indexWidget(table.model().index(0, 0))
    assert isinstance(combo, QComboBox)
    assert not combo.isEditable()
    assert [combo.itemText(i) for i in range(combo.count())] == list(HYPERPARAMETERS)
    combo.setCurrentText("Depth")
    assert node.properties["hyperparameters"][0]["name"] == "Depth"
    editor.add_row()
    editor.move_row(-1)
    assert [r["name"] for r in node.properties["hyperparameters"]] == ["NTrees", "Depth"]
    combo = table.indexWidget(table.model().index(1, 0))
    combo.setCurrentText("Binning")
    assert node.properties["hyperparameters"][1]["name"] == "Binning"
    panel.close()


def test_optimizer_shows_automatic_output_path_on_metric_change(app):
    from PySide6.QtWidgets import QLabel
    graph = Graph("optimization", "Optimization", "loader")
    node = graph.add_node(NODE_SPECS["bdt_evaluate"], 0, 0)
    scene = GraphScene(graph)
    panel = PropertyEditor()
    panel.show_node(scene, node.id)
    assert "Output file: results/optimization.png" in [w.text() for w in panel.findChildren(QLabel)]
    panel._set_node_property("metric", "AUC")
    app.processEvents()
    assert "Output file: results/optimization.txt" in [w.text() for w in panel.findChildren(QLabel)]
    panel._set_node_property("output_name", "momentum")
    app.processEvents()
    assert "Output file: results/momentum.txt" in [w.text() for w in panel.findChildren(QLabel)]
    panel.close()


def test_legacy_external_blocks_are_not_offered_or_editable(app):
    from copy import deepcopy
    from analysis_studio.analysis_modules import EXTERNAL_OBJECT_BLOCKS
    from analysis_studio.registry import specs_for_scope
    assert not EXTERNAL_OBJECT_BLOCKS.intersection(s.key for s in specs_for_scope("loader"))
    graph = Graph("legacy", "Legacy", "loader")
    nodes = [graph.add_node(NODE_SPECS[key], 0, 0) for key in EXTERNAL_OBJECT_BLOCKS]
    scene = GraphScene(graph)
    panel = PropertyEditor()
    for node in nodes:
        before = deepcopy(node.properties)
        panel.show_node(scene, node.id)
        assert not panel.findChildren(QLineEdit)
        assert not panel.findChildren(RecipeTableEditor)
        assert node.properties == before
    panel.close()
