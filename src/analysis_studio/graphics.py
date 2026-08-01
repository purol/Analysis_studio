from __future__ import annotations

import json
import math

from PySide6.QtCore import QByteArray, QMimeData, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QDrag,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsPolygonItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
    QApplication,
    QInputDialog,
    QMenu,
    QTreeWidget,
    QTreeWidgetItem,
)

from .model import ForEachRegion, Graph, WorkflowEdge, WorkflowNode
from .registry import FOREACH_DEFAULTS, NODE_SPECS, specs_for_scope


MIME_BLOCK_TYPE = "application/x-analysis-studio-block"
MIME_SELECTION_TYPE = "application/x-analysis-studio-selection"
FOREACH_REGION_TYPE = "__foreach_region__"
NODE_WIDTH = 210.0
HEADER_HEIGHT = 38.0
PORT_RADIUS = 6.5
REGION_HEADER_HEIGHT = 34.0
RESIZE_SIZE = 14.0


class PortItem(QGraphicsEllipseItem):
    def __init__(
        self,
        node_item: "NodeItem",
        name: str,
        direction: str,
        index: int,
        count: int,
    ) -> None:
        super().__init__(-PORT_RADIUS, -PORT_RADIUS, 2 * PORT_RADIUS, 2 * PORT_RADIUS)
        self.node_item = node_item
        self.name = name
        self.direction = direction
        self.setParentItem(node_item)
        self.setBrush(QColor("#e8edf3"))
        self.setPen(QPen(QColor("#26323f"), 1.5))
        self.setZValue(3)
        y = HEADER_HEIGHT + (index + 1) * (node_item.height - HEADER_HEIGHT) / (count + 1)
        x = 0.0 if direction == "input" else NODE_WIDTH
        self.setPos(x, y)
        self.setToolTip(f"{direction}: {name}")

    def mousePressEvent(self, event) -> None:  # noqa: N802
        scene = self.scene()
        if isinstance(scene, GraphScene):
            scene.port_clicked(self)
            event.accept()
            return
        super().mousePressEvent(event)

    def center_in_scene(self) -> QPointF:
        return self.mapToScene(QPointF(0.0, 0.0))


class NodeItem(QGraphicsRectItem):
    def __init__(self, node: WorkflowNode) -> None:
        self.node = node
        spec = NODE_SPECS[node.type]
        rows = max(len(spec.inputs), len(spec.outputs), 1)
        self.height = max(92.0, HEADER_HEIGHT + rows * 28.0)
        super().__init__(0.0, 0.0, NODE_WIDTH, self.height)

        self.setBrush(QColor("#202733"))
        self.setPen(QPen(QColor("#11161d"), 1.5))
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setPos(node.x, node.y)
        self.setZValue(2)

        header = QGraphicsRectItem(0.0, 0.0, NODE_WIDTH, HEADER_HEIGHT, self)
        header.setBrush(QColor(spec.color))
        header.setPen(Qt.PenStyle.NoPen)

        self.title_item = QGraphicsTextItem(node.title, self)
        self.title_item.setDefaultTextColor(QColor("white"))
        self.title_item.setPos(10.0, 7.0)
        self.title_item.setTextWidth(NODE_WIDTH - 48.0)

        self.type_item = QGraphicsTextItem(spec.label, self)
        self.type_item.setDefaultTextColor(QColor("#b8c2cf"))
        self.type_item.setPos(10.0, HEADER_HEIGHT + 9.0)

        self.badge_circle = QGraphicsEllipseItem(
            NODE_WIDTH - 31.0, 6.0, 24.0, 24.0, self
        )
        self.badge_circle.setBrush(QColor("#f4c95d"))
        self.badge_circle.setPen(QPen(QColor("#5b4710"), 1.0))
        self.badge_circle.setVisible(False)
        self.badge_text = QGraphicsTextItem("", self)
        self.badge_text.setDefaultTextColor(QColor("#1a1a1a"))
        self.badge_text.setPos(NODE_WIDTH - 27.5, 5.0)
        self.badge_text.setTextWidth(18.0)
        self.badge_text.setVisible(False)
        self.badge_circle.setToolTip("Start order")
        self.badge_text.setToolTip("Start order")

        self.input_ports: dict[str, PortItem] = {}
        self.output_ports: dict[str, PortItem] = {}
        for index, name in enumerate(spec.inputs):
            self.input_ports[name] = PortItem(self, name, "input", index, len(spec.inputs))
        for index, name in enumerate(spec.outputs):
            self.output_ports[name] = PortItem(self, name, "output", index, len(spec.outputs))

    def set_title(self, title: str) -> None:
        self.node.title = title
        self.title_item.setPlainText(title)

    def set_start_order(self, order: int | None) -> None:
        visible = order is not None
        self.badge_circle.setVisible(visible)
        self.badge_text.setVisible(visible)
        self.badge_text.setPlainText(str(order) if order is not None else "")

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        scene = self.scene()
        if not isinstance(scene, GraphScene):
            super().contextMenuEvent(event)
            return
        menu = QMenu()
        roots = scene.start_roots()
        is_root = any(root.id == self.node.id for root in roots)
        if is_root:
            action = menu.addAction("Set start order…")
            chosen = menu.exec(event.screenPos())
            if chosen == action:
                current = next(
                    index for index, root in enumerate(roots, start=1)
                    if root.id == self.node.id
                )
                value, accepted = QInputDialog.getInt(
                    None,
                    "Start order",
                    "Start order:",
                    current,
                    1,
                    max(1, len(roots)),
                    1,
                )
                if accepted:
                    scene.graph.set_root_order(self.node.id, value)
                    scene.refresh_start_badges()
                    scene.graph_changed.emit()
            return
        menu.addAction("Start order is available only for a start node").setEnabled(False)
        menu.exec(event.screenPos())

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        scene = self.scene()
        if isinstance(scene, GraphScene):
            scene.node_activated.emit(self.node.id)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def itemChange(self, change, value):  # noqa: N802
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.node.x = float(value.x())
            self.node.y = float(value.y())
            scene = self.scene()
            if isinstance(scene, GraphScene):
                scene.node_position_changed(self.node.id)
        return super().itemChange(change, value)


class ResizeHandle(QGraphicsRectItem):
    def __init__(self, region_item: "ForEachRegionItem") -> None:
        super().__init__(0.0, 0.0, RESIZE_SIZE, RESIZE_SIZE, region_item)
        self.region_item = region_item
        self.setBrush(QColor("#6fa3c8"))
        self.setPen(Qt.PenStyle.NoPen)
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self.setZValue(5)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        point = self.mapToParent(event.pos())
        self.region_item.resize_to(
            max(300.0, point.x() + RESIZE_SIZE / 2),
            max(150.0, point.y() + RESIZE_SIZE / 2),
        )
        event.accept()


class ForEachRegionItem(QGraphicsRectItem):
    def __init__(self, region: ForEachRegion) -> None:
        self.region = region
        self._last_position = QPointF(region.x, region.y)
        self._moving_members = False
        super().__init__(0.0, 0.0, region.width, region.height)
        self.setPos(region.x, region.y)
        self.setBrush(QColor(52, 82, 120, 35))
        self.setPen(QPen(QColor("#5f91bd"), 2.0, Qt.PenStyle.DashLine))
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setZValue(-2)

        self.header = QGraphicsRectItem(
            0.0, 0.0, region.width, REGION_HEADER_HEIGHT, self
        )
        self.header.setBrush(QColor(48, 79, 116, 190))
        self.header.setPen(Qt.PenStyle.NoPen)
        self.title_item = QGraphicsTextItem("", self)
        self.title_item.setDefaultTextColor(QColor("#eef5fb"))
        self.title_item.setPos(10.0, 5.0)
        self.summary_item = QGraphicsTextItem("", self)
        self.summary_item.setDefaultTextColor(QColor("#a8c4db"))
        self.summary_item.setPos(185.0, 5.0)
        self.resize_handle = ResizeHandle(self)
        self.refresh_label()
        self._position_handle()

    def refresh_label(self) -> None:
        self.title_item.setPlainText(f"For Each · {self.region.title}")
        mode = str(self.region.properties.get("source_mode", "root_files"))
        if mode == "root_files":
            detail = str(self.region.properties.get("pattern", "*.root"))
        elif mode == "csv_rows":
            detail = str(self.region.properties.get("csv_file", "items.csv"))
        else:
            count = len(
                [
                    line for line in str(self.region.properties.get("values", "")).splitlines()
                    if line.strip()
                ]
            )
            detail = f"{count} values"
        raw_tokens = self.region.properties.get("tokens", [])
        names = [
            str(item.get("name", "")).strip()
            for item in raw_tokens
            if isinstance(item, dict) and str(item.get("name", "")).strip()
        ] if isinstance(raw_tokens, list) else []
        variable_summary = ", ".join("{" + name + "}" for name in names[:3])
        if len(names) > 3:
            variable_summary += f", +{len(names) - 3}"
        suffix = f" · {variable_summary}" if variable_summary else " · no variables"
        self.summary_item.setPlainText(f"{mode} · {detail}{suffix}")

    def set_title(self, title: str) -> None:
        self.region.title = title
        self.refresh_label()

    def resize_to(self, width: float, height: float) -> None:
        self.region.width = float(width)
        self.region.height = float(height)
        self.setRect(0.0, 0.0, width, height)
        self.header.setRect(0.0, 0.0, width, REGION_HEADER_HEIGHT)
        self._position_handle()
        scene = self.scene()
        if isinstance(scene, GraphScene):
            scene.refresh_region_memberships()
            scene.graph_changed.emit()

    def _position_handle(self) -> None:
        self.resize_handle.setPos(
            self.region.width - RESIZE_SIZE,
            self.region.height - RESIZE_SIZE,
        )

    def itemChange(self, change, value):  # noqa: N802
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            new_position = QPointF(value)
            delta = new_position - self._last_position
            self._last_position = new_position
            self.region.x = float(new_position.x())
            self.region.y = float(new_position.y())
            scene = self.scene()
            if (
                isinstance(scene, GraphScene)
                and not self._moving_members
                and not scene._suspend_region_cascade
            ):
                self._moving_members = True
                scene.move_region_members(self.region.id, delta)
                self._moving_members = False
                scene.refresh_region_memberships()
                scene.graph_changed.emit()
        return super().itemChange(change, value)


class EdgeItem(QGraphicsPathItem):
    """A single cubic edge plus a separate arrowhead item.

    In v0.6 the arrow polygon was added to the same painter path and the path
    also had a brush. Qt implicitly closed the open Bézier subpath while
    filling it, which could look like a second straight edge. Keeping the
    arrowhead as a child polygon removes that unwanted chord.
    """

    def __init__(
        self,
        edge: WorkflowEdge,
        source_port: PortItem,
        target_port: PortItem,
    ) -> None:
        super().__init__()
        self.edge = edge
        self.source_port = source_port
        self.target_port = target_port
        self._normal_color = QColor("#86b7d9")
        self.setPen(QPen(self._normal_color, 3.0))
        self.setBrush(Qt.BrushStyle.NoBrush)
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setZValue(0)
        self.arrow_item = QGraphicsPolygonItem(self)
        self.arrow_item.setPen(Qt.PenStyle.NoPen)
        self.arrow_item.setBrush(self._normal_color)
        self.arrow_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.arrow_item.setZValue(1)
        self.update_path()

    def update_path(self) -> None:
        start = self.source_port.center_in_scene()
        end = self.target_port.center_in_scene()
        dx = max(70.0, abs(end.x() - start.x()) * 0.5)
        path = QPainterPath(start)
        path.cubicTo(
            QPointF(start.x() + dx, start.y()),
            QPointF(end.x() - dx, end.y()),
            end,
        )
        self.setPath(path)

        before_end = path.pointAtPercent(0.965)
        angle = math.atan2(end.y() - before_end.y(), end.x() - before_end.x())
        arrow_length = 15.0
        arrow_half_width = 7.0
        base = QPointF(
            end.x() - arrow_length * math.cos(angle),
            end.y() - arrow_length * math.sin(angle),
        )
        perpendicular = QPointF(
            arrow_half_width * math.sin(angle),
            -arrow_half_width * math.cos(angle),
        )
        self.arrow_item.setPolygon(
            QPolygonF([end, base + perpendicular, base - perpendicular])
        )

    def itemChange(self, change, value):  # noqa: N802
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            color = QColor("#f4c95d") if bool(value) else self._normal_color
            self.setPen(QPen(color, 3.0))
            self.arrow_item.setBrush(color)
        return super().itemChange(change, value)


class GraphScene(QGraphicsScene):
    node_selected = Signal(str)
    region_selected = Signal(str)
    node_added = Signal(str)
    node_activated = Signal(str)
    graph_changed = Signal()
    status_message = Signal(str)

    def __init__(self, graph: Graph, parent=None) -> None:
        super().__init__(parent)
        self.graph = graph
        self.node_items: dict[str, NodeItem] = {}
        self.edge_items: dict[str, EdgeItem] = {}
        self.region_items: dict[str, ForEachRegionItem] = {}
        self.pending_port: PortItem | None = None
        self._suspend_membership = False
        self._suspend_region_cascade = False
        self.setSceneRect(-2000.0, -1500.0, 4000.0, 3000.0)
        self.selectionChanged.connect(self._selection_changed)
        self.rebuild()

    def rebuild(self) -> None:
        self.clear()
        self.node_items.clear()
        self.edge_items.clear()
        self.region_items.clear()
        self.pending_port = None
        for region in self.graph.foreach_regions:
            self._add_region_item(region)
        for node in self.graph.nodes:
            self._add_node_item(node)
        for edge in self.graph.edges:
            self._add_edge_item(edge)
        self.refresh_region_memberships()
        self.refresh_start_badges()

    def _add_node_item(self, node: WorkflowNode) -> NodeItem:
        item = NodeItem(node)
        self.addItem(item)
        self.node_items[node.id] = item
        return item

    def _add_region_item(self, region: ForEachRegion) -> ForEachRegionItem:
        item = ForEachRegionItem(region)
        self.addItem(item)
        self.region_items[region.id] = item
        return item

    def _add_edge_item(self, edge: WorkflowEdge) -> EdgeItem:
        source = self.node_items[edge.source].output_ports[edge.source_port]
        target = self.node_items[edge.target].input_ports[edge.target_port]
        item = EdgeItem(edge, source, target)
        self.addItem(item)
        self.edge_items[edge.id] = item
        return item

    def add_block(self, block_type: str, position: QPointF) -> WorkflowNode:
        spec = NODE_SPECS[block_type]
        if spec.scope != self.graph.scope:
            raise ValueError(f"{spec.label} cannot be added to a {self.graph.scope} graph.")
        self.clearSelection()
        node = self.graph.add_node(spec, position.x(), position.y())
        item = self._add_node_item(node)
        item.setSelected(True)
        self.refresh_region_memberships()
        self.refresh_start_badges()
        self.graph_changed.emit()
        self.node_added.emit(node.id)
        return node

    def add_foreach_region(self, position: QPointF) -> ForEachRegion:
        self.clearSelection()
        region = self.graph.add_region(
            position.x(), position.y(), dict(FOREACH_DEFAULTS)
        )
        item = self._add_region_item(region)
        item.setSelected(True)
        self.refresh_region_memberships()
        self.graph_changed.emit()
        return region

    def port_clicked(self, port: PortItem) -> None:
        if self.pending_port is None:
            self.pending_port = port
            port.setBrush(QColor("#f4c95d"))
            self.status_message.emit("Select a port with the opposite direction.")
            return

        first = self.pending_port
        first.setBrush(QColor("#e8edf3"))
        self.pending_port = None
        if first.direction == port.direction:
            self.status_message.emit("Connect an output port to an input port.")
            return

        source = first if first.direction == "output" else port
        target = port if port.direction == "input" else first
        try:
            edge = self.graph.add_edge(
                source.node_item.node.id,
                source.name,
                target.node_item.node.id,
                target.name,
            )
        except ValueError as exc:
            self.status_message.emit(str(exc))
            return
        self._add_edge_item(edge)
        self.refresh_start_badges()
        self.graph_changed.emit()
        self.status_message.emit("Blocks connected.")

    def node_position_changed(self, node_id: str) -> None:
        self.update_edges_for_node(node_id)
        if not self._suspend_membership:
            self.refresh_region_memberships()
        self.graph_changed.emit()

    def update_edges_for_node(self, node_id: str) -> None:
        for edge in self.graph.incoming(node_id) + self.graph.outgoing(node_id):
            item = self.edge_items.get(edge.id)
            if item:
                item.update_path()

    def move_region_members(self, region_id: str, delta: QPointF) -> None:
        node_ids = self.graph.region_subtree_node_ids(region_id)
        descendant_ids = [
            region.id for region in self.graph.descendant_regions(region_id)
        ]
        self._suspend_membership = True
        self._suspend_region_cascade = True
        try:
            for descendant_id in descendant_ids:
                item = self.region_items.get(descendant_id)
                if item:
                    item.setPos(item.pos() + delta)
            for node_id in node_ids:
                item = self.node_items.get(node_id)
                if item:
                    item.setPos(item.pos() + delta)
        finally:
            self._suspend_region_cascade = False
            self._suspend_membership = False

    def refresh_region_memberships(self) -> None:
        if self.graph.scope != "workflow" or self._suspend_membership:
            return

        # A region is nested only when its complete rectangle is contained by
        # another region. The smallest containing rectangle is its direct parent.
        region_items = list(self.region_items.values())
        for child_item in region_items:
            child_rect = child_item.sceneBoundingRect()
            child_area = child_item.region.width * child_item.region.height
            candidates = [
                parent_item
                for parent_item in region_items
                if parent_item is not child_item
                and parent_item.region.width * parent_item.region.height > child_area
                and parent_item.sceneBoundingRect().contains(child_rect)
            ]
            child_item.region.parent_region_id = (
                min(
                    candidates,
                    key=lambda candidate: candidate.region.width
                    * candidate.region.height,
                ).region.id
                if candidates
                else None
            )

        for region in self.graph.foreach_regions:
            region.member_node_ids = []
        for node_id, item in self.node_items.items():
            center = item.sceneBoundingRect().center()
            candidates = [
                region_item
                for region_item in region_items
                if region_item.sceneBoundingRect().contains(center)
            ]
            if not candidates:
                continue
            chosen = min(
                candidates,
                key=lambda candidate: candidate.region.width
                * candidate.region.height,
            )
            chosen.region.member_node_ids.append(node_id)

        # Outer borders stay behind their children, making the nested structure
        # visible and allowing the smaller region to be selected naturally.
        for region_id, item in self.region_items.items():
            try:
                depth = self.graph.region_depth(region_id)
            except (KeyError, ValueError):
                depth = 0
            item.setZValue(-10.0 + depth)
            item.refresh_label()

    def start_roots(self) -> list[WorkflowNode]:
        if self.graph.scope != "workflow":
            return self.graph.ordered_roots()
        from .validation import workflow_dependency_pairs

        try:
            pairs = workflow_dependency_pairs(self.graph)
            extra = [pair for pair in pairs if pair not in self.graph.dependency_pairs()]
            return self.graph.ordered_roots(extra)
        except ValueError:
            # Keep an incomplete graph editable; validation will show the exact
            # unresolved Wait reference.
            return self.graph.ordered_roots()

    def refresh_start_badges(self) -> None:
        self.graph.normalize_root_order()
        order = {
            node.id: index
            for index, node in enumerate(self.start_roots(), start=1)
        }
        for node_id, item in self.node_items.items():
            item.set_start_order(order.get(node_id))


    def copy_selected(self) -> bool:
        selected_nodes = [
            item.node for item in self.selectedItems() if isinstance(item, NodeItem)
        ]
        if not selected_nodes:
            self.status_message.emit("Select one or more blocks to copy.")
            return False
        selected_ids = {node.id for node in selected_nodes}
        edges = [
            edge for edge in self.graph.edges
            if edge.source in selected_ids and edge.target in selected_ids
        ]
        payload = {
            "scope": self.graph.scope,
            "nodes": [
                {
                    "old_id": node.id,
                    "type": node.type,
                    "title": node.title,
                    "x": node.x,
                    "y": node.y,
                    "properties": node.properties,
                }
                for node in selected_nodes
            ],
            "edges": [
                {
                    "source": edge.source,
                    "source_port": edge.source_port,
                    "target": edge.target,
                    "target_port": edge.target_port,
                }
                for edge in edges
            ],
        }
        mime = QMimeData()
        mime.setData(
            MIME_SELECTION_TYPE,
            QByteArray(json.dumps(payload, ensure_ascii=False).encode("utf-8")),
        )
        QApplication.clipboard().setMimeData(mime)
        self.status_message.emit(
            f"Copied {len(selected_nodes)} block(s). Internal IDs will be regenerated."
        )
        return True

    def paste_selected(self) -> list[WorkflowNode]:
        mime = QApplication.clipboard().mimeData()
        if not mime.hasFormat(MIME_SELECTION_TYPE):
            self.status_message.emit("The clipboard does not contain Analysis Studio blocks.")
            return []
        try:
            payload = json.loads(
                bytes(mime.data(MIME_SELECTION_TYPE)).decode("utf-8")
            )
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
            self.status_message.emit(f"Could not paste blocks: {exc}")
            return []
        if payload.get("scope") != self.graph.scope:
            self.status_message.emit(
                "Blocks can be pasted only into a graph of the same type."
            )
            return []

        raw_nodes = list(payload.get("nodes", []))
        if not raw_nodes:
            return []
        minimum_x = min(float(item.get("x", 0.0)) for item in raw_nodes)
        minimum_y = min(float(item.get("y", 0.0)) for item in raw_nodes)
        # Repeated Ctrl+V remains visible instead of exactly covering the source.
        offset = QPointF(36.0, 36.0)
        id_map: dict[str, str] = {}
        created: list[WorkflowNode] = []
        self.clearSelection()
        for raw in raw_nodes:
            block_type = str(raw.get("type", ""))
            spec = NODE_SPECS.get(block_type)
            if spec is None or spec.scope != self.graph.scope:
                continue
            node = self.graph.add_node(
                spec,
                float(raw.get("x", minimum_x)) + offset.x(),
                float(raw.get("y", minimum_y)) + offset.y(),
                str(raw.get("title", spec.label)),
            )
            node.properties = dict(raw.get("properties", {}))
            id_map[str(raw.get("old_id", ""))] = node.id
            created.append(node)
            item = self._add_node_item(node)
            item.setSelected(True)

        for node in created:
            if node.type != "wait":
                continue
            references = str(node.properties.get("wait_for", "")).splitlines()
            node.properties["wait_for"] = "\n".join(
                id_map.get(reference.strip(), reference)
                for reference in references
            )

        for raw_edge in payload.get("edges", []):
            source = id_map.get(str(raw_edge.get("source", "")))
            target = id_map.get(str(raw_edge.get("target", "")))
            if not source or not target:
                continue
            try:
                edge = self.graph.add_edge(
                    source,
                    str(raw_edge.get("source_port", "out")),
                    target,
                    str(raw_edge.get("target_port", "in")),
                )
            except ValueError:
                continue
            self._add_edge_item(edge)

        if created:
            self.refresh_region_memberships()
            self.refresh_start_badges()
            self.graph_changed.emit()
            self.node_added.emit(created[-1].id)
            self.status_message.emit(
                f"Pasted {len(created)} block(s) with new internal IDs."
            )
        return created

    def delete_selected(self) -> None:
        selected = list(self.selectedItems())
        edge_ids = [item.edge.id for item in selected if isinstance(item, EdgeItem)]
        node_ids = [item.node.id for item in selected if isinstance(item, NodeItem)]
        region_ids = [
            item.region.id for item in selected if isinstance(item, ForEachRegionItem)
        ]
        for edge_id in edge_ids:
            self.graph.remove_edge(edge_id)
        for node_id in node_ids:
            self.graph.remove_node(node_id)
        for region_id in region_ids:
            self.graph.remove_region(region_id)
        if edge_ids or node_ids or region_ids:
            self.rebuild()
            self.graph_changed.emit()

    def _selection_changed(self) -> None:
        for item in self.selectedItems():
            if isinstance(item, NodeItem):
                self.region_selected.emit("")
                self.node_selected.emit(item.node.id)
                return
            if isinstance(item, ForEachRegionItem):
                self.node_selected.emit("")
                self.region_selected.emit(item.region.id)
                return
        self.node_selected.emit("")
        self.region_selected.emit("")


class GraphView(QGraphicsView):
    def __init__(self, scene: GraphScene, parent=None) -> None:
        super().__init__(scene, parent)
        self.setAcceptDrops(True)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setBackgroundBrush(QColor("#151a21"))

    def wheelEvent(self, event) -> None:  # noqa: N802
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            self.scale(factor, factor)
            event.accept()
            return
        super().wheelEvent(event)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasFormat(MIME_BLOCK_TYPE):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasFormat(MIME_BLOCK_TYPE):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        if not event.mimeData().hasFormat(MIME_BLOCK_TYPE):
            super().dropEvent(event)
            return
        block_type = bytes(event.mimeData().data(MIME_BLOCK_TYPE)).decode("utf-8")
        position = self.mapToScene(event.position().toPoint())
        scene = self.scene()
        if isinstance(scene, GraphScene):
            if block_type == FOREACH_REGION_TYPE:
                scene.add_foreach_region(position)
            else:
                scene.add_block(block_type, position)
        event.acceptProposedAction()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        scene = self.scene()
        if isinstance(scene, GraphScene):
            if event.matches(QKeySequence.StandardKey.Copy):
                scene.copy_selected()
                event.accept()
                return
            if event.matches(QKeySequence.StandardKey.Paste):
                scene.paste_selected()
                event.accept()
                return
            if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                scene.delete_selected()
                event.accept()
                return
        super().keyPressEvent(event)


class BlockPalette(QTreeWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setDragEnabled(True)
        self.setMinimumWidth(205)
        self.scope = "workflow"
        self.set_scope(self.scope)

    def set_scope(self, scope: str) -> None:
        self.scope = scope
        self.clear()
        categories: dict[str, QTreeWidgetItem] = {}
        for spec in specs_for_scope(scope):
            parent = categories.get(spec.category)
            if parent is None:
                parent = QTreeWidgetItem([spec.category])
                parent.setFlags(parent.flags() & ~Qt.ItemFlag.ItemIsDragEnabled)
                categories[spec.category] = parent
                self.addTopLevelItem(parent)
            item = QTreeWidgetItem([spec.label])
            item.setData(0, Qt.ItemDataRole.UserRole, spec.key)
            item.setForeground(0, QBrush(QColor(spec.color).lighter(150)))
            parent.addChild(item)
        if scope == "workflow":
            parent = categories.get("Control")
            if parent is None:
                parent = QTreeWidgetItem(["Control"])
                parent.setFlags(parent.flags() & ~Qt.ItemFlag.ItemIsDragEnabled)
                self.addTopLevelItem(parent)
            item = QTreeWidgetItem(["For Each Region"])
            item.setData(0, Qt.ItemDataRole.UserRole, FOREACH_REGION_TYPE)
            item.setForeground(0, QBrush(QColor("#5f91bd").lighter(150)))
            item.setToolTip(0, "Drag a compact loop border around workflow blocks.")
            parent.addChild(item)
        self.expandAll()

    def startDrag(self, supported_actions) -> None:  # noqa: N802
        item = self.currentItem()
        if item is None:
            return
        block_type = item.data(0, Qt.ItemDataRole.UserRole)
        if not block_type:
            return
        mime = QMimeData()
        mime.setData(MIME_BLOCK_TYPE, QByteArray(str(block_type).encode("utf-8")))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)
