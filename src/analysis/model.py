from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
import hashlib
import itertools
import json
import os
import time


_id_counter = itertools.count()


def new_id(prefix: str) -> str:
    # Avoid a hard dependency on /dev/urandom. Some batch/HPC sandboxes expose
    # it late or not at all, while time + process + monotonic counter is enough
    # to identify GUI objects within a project.
    seed = f"{time.time_ns()}:{os.getpid()}:{next(_id_counter)}".encode()
    digest = hashlib.sha1(seed).hexdigest()[:12]
    return f"{prefix}_{digest}"


@dataclass(frozen=True)
class PropertySpec:
    """ Parameter in block """

    name: str # Internal key used in JSON
    label: str # Name displayed in the GUI

    kind: str = "text" # text, choice, int, float, bool, or path
    default: object = ""

    # Used only when kind == "choice"
    choices: tuple[str, ...] = ()

    # Option to write multiple line
    multiline: bool = False

    help: str = ""


@dataclass(frozen=True)
class NodeSpec:
    """ Block in GUI """

    key: str # Internal key used in JSON
    label: str # Name displayed in the GUI

    # Group upder which the block appears in the block palette
    category: str

    # Scope in which the block can be used
    # Currently either "workflow" or "loader"
    scope: str

    color: str

    # Names of the input and output ports in the block
    inputs: tuple[str, ...] = ("in",)
    outputs: tuple[str, ...] = ("out",)

    properties: tuple[PropertySpec, ...] = ()

    def defaults(self) -> dict[str, object]:
        return {item.name: item.default for item in self.properties}


@dataclass
class WorkflowNode:
    """ block instance in a workflow graph """

    # Unique identifier
    id: str

    # Block type corresponding to NodeSpec.key
    type: str

    # Editable name displayed in the block header
    title: str

    # Position of the block
    x: float = 0.0
    y: float = 0.0
    
    properties: dict[str, object] = field(default_factory=dict)


@dataclass
class WorkflowEdge:
    id: str
    source: str
    source_port: str
    target: str
    target_port: str


@dataclass
class Graph:
    id: str
    name: str
    scope: str
    nodes: list[WorkflowNode] = field(default_factory=list)
    edges: list[WorkflowEdge] = field(default_factory=list)

    def node(self, node_id: str) -> WorkflowNode:
        for item in self.nodes:
            if item.id == node_id:
                return item
        raise KeyError(node_id)

    def add_node(
        self,
        spec: NodeSpec,
        x: float,
        y: float,
        title: str | None = None,
    ) -> WorkflowNode:
        node = WorkflowNode(
            id=new_id("node"),
            type=spec.key,
            title=title or spec.label,
            x=x,
            y=y,
            properties=spec.defaults(),
        )
        self.nodes.append(node)
        return node

    def add_edge(
        self,
        source: str,
        source_port: str,
        target: str,
        target_port: str,
    ) -> WorkflowEdge:
        if source == target:
            raise ValueError("A node cannot connect to itself.")
        if any(
            edge.source == source
            and edge.source_port == source_port
            and edge.target == target
            and edge.target_port == target_port
            for edge in self.edges
        ):
            raise ValueError("That connection already exists.")

        candidate = WorkflowEdge(
            id=new_id("edge"),
            source=source,
            source_port=source_port,
            target=target,
            target_port=target_port,
        )
        self.edges.append(candidate)
        try:
            self.topological_order()
        except ValueError:
            self.edges.remove(candidate)
            raise
        return candidate

    def remove_node(self, node_id: str) -> None:
        self.nodes = [node for node in self.nodes if node.id != node_id]
        self.edges = [
            edge
            for edge in self.edges
            if edge.source != node_id and edge.target != node_id
        ]

    def remove_edge(self, edge_id: str) -> None:
        self.edges = [edge for edge in self.edges if edge.id != edge_id]

    def incoming(self, node_id: str) -> list[WorkflowEdge]:
        return [edge for edge in self.edges if edge.target == node_id]

    def outgoing(self, node_id: str) -> list[WorkflowEdge]:
        return [edge for edge in self.edges if edge.source == node_id]

    def topological_order(self) -> list[WorkflowNode]:
        indegree = {node.id: 0 for node in self.nodes}
        children: dict[str, list[str]] = {node.id: [] for node in self.nodes}
        for edge in self.edges:
            if edge.source not in indegree or edge.target not in indegree:
                raise ValueError("Graph contains an edge to a missing node.")
            indegree[edge.target] += 1
            children[edge.source].append(edge.target)

        ready = [node.id for node in self.nodes if indegree[node.id] == 0]
        ordered: list[WorkflowNode] = []
        while ready:
            node_id = ready.pop(0)
            ordered.append(self.node(node_id))
            for child in children[node_id]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)

        if len(ordered) != len(self.nodes):
            raise ValueError("Cycles are not allowed in a workflow.")
        return ordered

    def validate(self, registry: dict[str, NodeSpec]) -> list[str]:
        errors: list[str] = []
        try:
            self.topological_order()
        except ValueError as exc:
            errors.append(str(exc))

        node_ids = {node.id for node in self.nodes}
        for node in self.nodes:
            spec = registry.get(node.type)
            if spec is None:
                errors.append(f"{node.title}: unknown block type '{node.type}'.")
                continue
            if spec.scope != self.scope:
                errors.append(f"{node.title}: block belongs to '{spec.scope}', not '{self.scope}'.")
            for prop in spec.properties:
                if prop.name not in node.properties:
                    errors.append(f"{node.title}: missing property '{prop.name}'.")

        for edge in self.edges:
            if edge.source not in node_ids or edge.target not in node_ids:
                errors.append(f"{edge.id}: missing endpoint.")
                continue
            source_spec = registry.get(self.node(edge.source).type)
            target_spec = registry.get(self.node(edge.target).type)
            if source_spec and edge.source_port not in source_spec.outputs:
                errors.append(f"{edge.id}: invalid output port '{edge.source_port}'.")
            if target_spec and edge.target_port not in target_spec.inputs:
                errors.append(f"{edge.id}: invalid input port '{edge.target_port}'.")
        return errors


@dataclass
class Project:
    name: str
    workflow: Graph
    loader_graphs: dict[str, Graph]
    backend: str = "local"
    backend_options: dict[str, object] = field(
        default_factory=lambda: {
            "local_workers": 4,
            "lsf_queue": "s",
            "condor_universe": "vanilla",
        }
    )
    version: int = 1

    @classmethod
    def empty(cls, name: str = "Untitled analysis") -> "Project":
        pipeline = Graph(
            id="main_analysis",
            name="Main analysis",
            scope="loader",
        )
        return cls(
            name=name,
            workflow=Graph(id="workflow", name="Workflow", scope="workflow"),
            loader_graphs={pipeline.id: pipeline},
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "Project":
        def graph_from_dict(raw: dict[str, object]) -> Graph:
            return Graph(
                id=str(raw["id"]),
                name=str(raw["name"]),
                scope=str(raw["scope"]),
                nodes=[WorkflowNode(**item) for item in raw.get("nodes", [])],
                edges=[WorkflowEdge(**item) for item in raw.get("edges", [])],
            )

        workflow = graph_from_dict(data["workflow"])
        loader_graphs = {
            key: graph_from_dict(value)
            for key, value in data.get("loader_graphs", {}).items()
        }
        return cls(
            name=str(data.get("name", "Untitled analysis")),
            workflow=workflow,
            loader_graphs=loader_graphs,
            backend=str(data.get("backend", "local")),
            backend_options=dict(data.get("backend_options", {})),
            version=int(data.get("version", 1)),
        )

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: str | Path) -> "Project":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
