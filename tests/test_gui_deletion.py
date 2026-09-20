"""Regression coverage for selection signals while deleting graph items."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QInputDialog

from analysis_studio.main_window import MainWindow


@pytest.fixture
def window(monkeypatch):
    app = QApplication.instance() or QApplication([])
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *error: errors.append(error))
    ui = MainWindow()
    monkeypatch.setattr(QInputDialog, "getText", lambda *_: ("Deletion regression", True))
    ui.add_loader_program()
    yield app, ui, errors
    ui._reset_history(saved=True)
    ui.close()
    app.processEvents()


@pytest.mark.parametrize("delete_all", [True, False])
def test_delete_key_never_announces_removed_nodes_and_clears_properties(window, delete_all):
    app, ui, errors = window
    editor = ui._active_editor()
    scene = editor.scene
    original_ids = set(scene.node_items)
    selected_ids = original_ids if delete_all else {next(iter(original_ids))}
    for node_id in selected_ids:
        scene.node_items[node_id].setSelected(True)
    if delete_all:
        for item in scene.edge_items.values():
            item.setSelected(True)
    assert ui.properties.node is not None
    stale_signals = []
    scene.node_selected.connect(lambda node_id: stale_signals.append(node_id)
                                if node_id and node_id not in {n.id for n in scene.graph.nodes} else None)
    QTest.keyClick(editor.view, Qt.Key.Key_Delete)
    app.processEvents()
    assert not errors
    assert not stale_signals
    assert {n.id for n in scene.graph.nodes} == original_ids - selected_ids
    assert set(scene.node_items) == original_ids - selected_ids
    assert not scene.selectedItems()
    assert ui.properties.node is None
    assert ui.properties.scene is None

    # The rebuilt canvas must still accept and edit new blocks.
    new_node = scene.add_block("bdt_evaluate", QPointF(0, 0))
    assert ui.properties.node is new_node
    assert not errors


def test_queued_property_refresh_does_not_reopen_deleted_block(window):
    app, ui, errors = window
    scene = ui._active_editor().scene
    node = scene.add_block("bdt_evaluate", QPointF(0, 0))
    ui.properties._set_node_property("metric", "AUC")
    scene.delete_selected()
    replacement = scene.add_block("samples", QPointF(100, 0))
    app.processEvents()
    assert not errors
    assert node.id not in scene.node_items
    assert ui.properties.node is replacement


def test_deselecting_block_clears_property_panel(window):
    app, ui, errors = window
    scene = ui._active_editor().scene
    scene.add_block("samples", QPointF(0, 0))
    scene.clearSelection()
    assert ui.properties.node is None
    assert not errors


def test_delete_foreach_with_pending_refresh_keeps_new_selection(window):
    app, ui, errors = window
    ui.tabs.setCurrentIndex(0)
    scene = ui._active_editor().scene
    region = scene.add_foreach_region(QPointF(0, 0))
    assert ui.properties.region is region
    ui.properties._set_region_property("source_mode", region.properties["source_mode"])
    scene.delete_selected()
    assert ui.properties.region is None
    replacement = scene.add_foreach_region(QPointF(100, 0))
    app.processEvents()
    assert not errors
    assert region.id not in scene.region_items
    assert ui.properties.region is replacement
