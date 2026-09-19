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
