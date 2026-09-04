from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
import hashlib
import itertools
import json
import os
import re
import shlex
import time
from typing import Iterable


_id_counter = itertools.count()


def new_id(prefix: str) -> str:
    seed = f"{time.time_ns()}:{os.getpid()}:{next(_id_counter)}".encode()
    digest = hashlib.sha1(seed).hexdigest()[:12]
    return f"{prefix}_{digest}"


def safe_program_name(value: str, fallback: str = "loader_program") -> str:
    """Return the deterministic source/executable stem for a Loader program."""
    result = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("._")
    return result or fallback


def loader_source_filename(graph: "Graph") -> str:
    return f"{safe_program_name(graph.name)}.cc"


def loader_executable_path(graph: "Graph") -> str:
    return f"./bin/{safe_program_name(graph.name)}"


def custom_command_output_names(project: "Project") -> dict[str, str]:
    """Return deterministic bin names derived from Custom Command source names.

    The first distinct source named ``fit.cc`` owns ``fit``. A different source
    with the same stem receives ``fit_2``, then ``fit_3``, and so on. Multiple
    workflow blocks that invoke the same source share one built artifact. Loader
    executable names are reserved so Custom Commands cannot silently overwrite
    them.
    """
    used = {
        safe_program_name(graph.name)
        for graph in project.loader_programs.values()
        if graph.nodes
    }
    source_names: dict[str, str] = {}
    result: dict[str, str] = {}
    for node in project.workflow.nodes:
        if node.type != "custom_command":
            continue
        raw_source = str(node.properties.get("code", "")).strip()
        source_key = Path(raw_source or "custom_command").as_posix()
        assigned = source_names.get(source_key)
        if assigned is None:
            base = safe_program_name(Path(source_key).stem, "custom_command")
            assigned = base
            suffix = 2
            while assigned in used:
                assigned = f"{base}_{suffix}"
                suffix += 1
            source_names[source_key] = assigned
            used.add(assigned)
        result[node.id] = assigned
    return result


def custom_command_output_name(
    node: "WorkflowNode", project: "Project | None" = None
) -> str:
    if project is not None:
        return custom_command_output_names(project).get(node.id, "custom_command")
    source = Path(str(node.properties.get("code", "custom_command")))
    return safe_program_name(source.stem, "custom_command")


def custom_executable_path(
    node: "WorkflowNode", project: "Project | None" = None
) -> str:
    return f"./bin/{custom_command_output_name(node, project)}"


@dataclass(frozen=True)
class PropertySpec:
    name: str
    label: str
    kind: str = "text"
    default: object = ""
    choices: tuple[str, ...] = ()
    multiline: bool = False
    help: str = ""


@dataclass(frozen=True)
class NodeSpec:
    key: str
    label: str
    category: str
    scope: str
    color: str
    inputs: tuple[str, ...] = ("in",)
    outputs: tuple[str, ...] = ("out",)
    properties: tuple[PropertySpec, ...] = ()

    def defaults(self) -> dict[str, object]:
        return {item.name: item.default for item in self.properties}


@dataclass
class WorkflowNode:
    id: str
    type: str
    title: str
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
class ForEachRegion:
    id: str
    title: str
    x: float
    y: float
    width: float = 560.0
    height: float = 260.0
    properties: dict[str, object] = field(default_factory=dict)
    # Direct node members only. Nodes inside a nested child region belong to
    # that child, while the parent contains the child region itself.
    member_node_ids: list[str] = field(default_factory=list)
    parent_region_id: str | None = None


@dataclass
class Graph:
    id: str
    name: str
    scope: str
    nodes: list[WorkflowNode] = field(default_factory=list)
    edges: list[WorkflowEdge] = field(default_factory=list)
    root_order: list[str] = field(default_factory=list)
    foreach_regions: list[ForEachRegion] = field(default_factory=list)

    def node(self, node_id: str) -> WorkflowNode:
        for item in self.nodes:
            if item.id == node_id:
                return item
        raise KeyError(node_id)

    def region(self, region_id: str) -> ForEachRegion:
        for item in self.foreach_regions:
            if item.id == region_id:
                return item
        raise KeyError(region_id)

    def direct_region_for_node(self, node_id: str) -> ForEachRegion | None:
        matches = [
            region for region in self.foreach_regions
            if node_id in region.member_node_ids
        ]
        if len(matches) > 1:
            raise ValueError(
                f"Node '{node_id}' belongs directly to more than one For Each region."
            )
        return matches[0] if matches else None

    def child_regions(self, parent_region_id: str | None) -> list[ForEachRegion]:
        return [
            region for region in self.foreach_regions
            if region.parent_region_id == parent_region_id
        ]

    def ancestor_regions(self, region_id: str) -> list[ForEachRegion]:
        result: list[ForEachRegion] = []
        current = self.region(region_id)
        visited = {current.id}
        while current.parent_region_id is not None:
            parent = self.region(current.parent_region_id)
            if parent.id in visited:
                raise ValueError("For Each region nesting contains a cycle.")
            visited.add(parent.id)
            result.append(parent)
            current = parent
        result.reverse()
        return result

    def descendant_regions(self, region_id: str) -> list[ForEachRegion]:
        result: list[ForEachRegion] = []
        stack = list(reversed(self.child_regions(region_id)))
        while stack:
            region = stack.pop()
            result.append(region)
            stack.extend(reversed(self.child_regions(region.id)))
        return result

    def region_depth(self, region_id: str) -> int:
        return len(self.ancestor_regions(region_id))

    def regions_for_node(self, node_id: str) -> list[ForEachRegion]:
        direct = self.direct_region_for_node(node_id)
        if direct is None:
            return []
        return [*self.ancestor_regions(direct.id), direct]

    def region_subtree_node_ids(self, region_id: str) -> set[str]:
        region_ids = {region_id, *(item.id for item in self.descendant_regions(region_id))}
        return {
            node_id
            for region in self.foreach_regions
            if region.id in region_ids
            for node_id in region.member_node_ids
        }

    def immediate_child_region_for_node(
        self, node_id: str, parent_region_id: str | None
    ) -> ForEachRegion | None:
        chain = self.regions_for_node(node_id)
        if parent_region_id is None:
            return chain[0] if chain else None
        for index, region in enumerate(chain):
            if region.id == parent_region_id:
                return chain[index + 1] if index + 1 < len(chain) else None
        return None

    def infer_region_hierarchy_from_geometry(self) -> None:
        """Infer immediate parents from fully contained region rectangles."""
        for child in self.foreach_regions:
            candidates: list[ForEachRegion] = []
            child_right = child.x + child.width
            child_bottom = child.y + child.height
            child_area = child.width * child.height
            for parent in self.foreach_regions:
                if parent.id == child.id:
                    continue
                parent_area = parent.width * parent.height
                if parent_area <= child_area:
                    continue
                if (
                    parent.x <= child.x
                    and parent.y <= child.y
                    and parent.x + parent.width >= child_right
                    and parent.y + parent.height >= child_bottom
                ):
                    candidates.append(parent)
            child.parent_region_id = (
                min(candidates, key=lambda item: item.width * item.height).id
                if candidates
                else None
            )

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
        self.normalize_root_order()
        return node

    def add_region(
        self,
        x: float,
        y: float,
        properties: dict[str, object],
        title: str = "For Each",
        width: float = 560.0,
        height: float = 260.0,
    ) -> ForEachRegion:
        if self.scope != "workflow":
            raise ValueError("For Each regions are only available in workflow graphs.")
        region = ForEachRegion(
            id=new_id("foreach"),
            title=title,
            x=x,
            y=y,
            width=width,
            height=height,
            properties=dict(properties),
        )
        self.foreach_regions.append(region)
        return region

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
        self.normalize_root_order()
        return candidate

    def remove_node(self, node_id: str) -> None:
        self.nodes = [node for node in self.nodes if node.id != node_id]
        self.edges = [
            edge
            for edge in self.edges
            if edge.source != node_id and edge.target != node_id
        ]
        for region in self.foreach_regions:
            region.member_node_ids = [
                member for member in region.member_node_ids if member != node_id
            ]
        self.root_order = [item for item in self.root_order if item != node_id]
        self.normalize_root_order()

    def remove_region(self, region_id: str) -> None:
        removed = self.region(region_id)
        for child in self.foreach_regions:
            if child.parent_region_id == region_id:
                child.parent_region_id = removed.parent_region_id
        self.foreach_regions = [
            region for region in self.foreach_regions if region.id != region_id
        ]

    def remove_edge(self, edge_id: str) -> None:
        self.edges = [edge for edge in self.edges if edge.id != edge_id]
        self.normalize_root_order()

    def incoming(self, node_id: str) -> list[WorkflowEdge]:
        return [edge for edge in self.edges if edge.target == node_id]

    def outgoing(self, node_id: str) -> list[WorkflowEdge]:
        return [edge for edge in self.edges if edge.source == node_id]

    def dependency_pairs(self) -> list[tuple[str, str]]:
        return [(edge.source, edge.target) for edge in self.edges]

    def roots(
        self,
        extra_dependencies: Iterable[tuple[str, str]] = (),
        node_ids: Iterable[str] | None = None,
    ) -> list[WorkflowNode]:
        allowed = set(node_ids) if node_ids is not None else {node.id for node in self.nodes}
        indegree = {node_id: 0 for node_id in allowed}
        for source, target in [*self.dependency_pairs(), *list(extra_dependencies)]:
            if source in allowed and target in allowed:
                indegree[target] += 1
        return [node for node in self.nodes if node.id in allowed and indegree[node.id] == 0]

    def normalize_root_order(self) -> None:
        roots = [node.id for node in self.roots()]
        self.root_order = [node_id for node_id in self.root_order if node_id in roots]
        self.root_order.extend(node_id for node_id in roots if node_id not in self.root_order)

    def ordered_roots(
        self,
        extra_dependencies: Iterable[tuple[str, str]] = (),
        node_ids: Iterable[str] | None = None,
    ) -> list[WorkflowNode]:
        roots = self.roots(extra_dependencies, node_ids)
        root_ids = {node.id for node in roots}
        order = [node_id for node_id in self.root_order if node_id in root_ids]
        order.extend(node.id for node in roots if node.id not in order)
        by_id = {node.id: node for node in roots}
        return [by_id[node_id] for node_id in order]

    def set_root_order(self, node_id: str, one_based_position: int) -> None:
        self.normalize_root_order()
        if node_id not in self.root_order:
            raise ValueError("Only a start node can have a start order.")
        items = [item for item in self.root_order if item != node_id]
        index = max(0, min(len(items), int(one_based_position) - 1))
        items.insert(index, node_id)
        self.root_order = items

    def topological_order(
        self,
        extra_dependencies: Iterable[tuple[str, str]] = (),
        node_ids: Iterable[str] | None = None,
    ) -> list[WorkflowNode]:
        allowed = set(node_ids) if node_ids is not None else {node.id for node in self.nodes}
        indegree = {node_id: 0 for node_id in allowed}
        children: dict[str, list[str]] = {node_id: [] for node_id in allowed}
        pairs = [*self.dependency_pairs(), *list(extra_dependencies)]
        seen: set[tuple[str, str]] = set()
        for source, target in pairs:
            if source not in allowed or target not in allowed:
                continue
            if (source, target) in seen:
                continue
            seen.add((source, target))
            indegree[target] += 1
            children[source].append(target)

        roots = self.ordered_roots(extra_dependencies, allowed)
        root_rank = {node.id: rank for rank, node in enumerate(roots)}
        branch_rank = {node_id: len(roots) for node_id in allowed}
        for root in roots:
            rank = root_rank[root.id]
            stack = [root.id]
            visited: set[str] = set()
            while stack:
                current = stack.pop()
                if current in visited:
                    continue
                visited.add(current)
                branch_rank[current] = min(branch_rank[current], rank)
                stack.extend(children.get(current, []))

        insertion_rank = {
            node.id: index for index, node in enumerate(self.nodes) if node.id in allowed
        }

        def sort_ready(values: list[str]) -> None:
            values.sort(key=lambda item: (branch_rank[item], insertion_rank[item]))

        ready = [node_id for node_id in allowed if indegree[node_id] == 0]
        sort_ready(ready)
        ordered: list[WorkflowNode] = []
        while ready:
            node_id = ready.pop(0)
            ordered.append(self.node(node_id))
            for child in children[node_id]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)
            sort_ready(ready)

        if len(ordered) != len(allowed):
            raise ValueError("Cycles are not allowed in a workflow.")
        return ordered

    def has_path(
        self,
        source: str,
        target: str,
        extra_dependencies: Iterable[tuple[str, str]] = (),
    ) -> bool:
        if source == target:
            return True
        children: dict[str, list[str]] = {node.id: [] for node in self.nodes}
        for start, end in [*self.dependency_pairs(), *list(extra_dependencies)]:
            if start in children:
                children[start].append(end)
        stack = [source]
        visited: set[str] = set()
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            for child in children.get(current, []):
                if child == target:
                    return True
                stack.append(child)
        return False

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
                errors.append(
                    f"{node.title}: block belongs to '{spec.scope}', not '{self.scope}'."
                )
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

        region_ids = {region.id for region in self.foreach_regions}
        region_members: dict[str, str] = {}
        for region in self.foreach_regions:
            if region.parent_region_id == region.id:
                errors.append(f"{region.title}: a For Each region cannot contain itself.")
            elif (
                region.parent_region_id is not None
                and region.parent_region_id not in region_ids
            ):
                errors.append(
                    f"{region.title}: parent region '{region.parent_region_id}' is missing."
                )
            for member_id in region.member_node_ids:
                if member_id not in node_ids:
                    errors.append(f"{region.title}: member block '{member_id}' is missing.")
                elif member_id in region_members:
                    errors.append(
                        f"{self.node(member_id).title}: a block cannot belong directly "
                        "to two For Each regions."
                    )
                else:
                    region_members[member_id] = region.id
        for region in self.foreach_regions:
            try:
                self.ancestor_regions(region.id)
            except (KeyError, ValueError) as exc:
                errors.append(f"{region.title}: {exc}")
        return errors


@dataclass
class Project:
    name: str
    workflow: Graph
    loader_programs: dict[str, Graph]
    backend: str = "local"
    backend_options: dict[str, object] = field(
        default_factory=lambda: {
            "local_workers": 4,
            "lsf_poll_seconds": 10,
            "lsf_max_active_jobs": 500,
            "lsf_cancel_on_failure": True,
            "condor_universe": "vanilla",
        }
    )
    build_options: dict[str, object] = field(
        default_factory=lambda: {
            "compiler": "g++",
            "cpp_standard": "c++17",
            "belle2_analysis_dir": "Belle2_analysis",
            "common_compile_flags": "",
            "common_link_flags": "",
            "loader_libraries": (
                "-lRooFit\n-lRooStats\n-lRooFitCore\n-lMinuit\n"
                "-lFastBDT_static"
            ),
        }
    )
    version: int = 10

    @classmethod
    def empty(cls, name: str = "Untitled analysis") -> "Project":
        return cls(
            name=name,
            workflow=Graph(id="workflow", name="Workflow", scope="workflow"),
            loader_programs={},
        )

    def create_loader_program(self, name: str) -> Graph:
        program_name = name.strip()
        if not program_name:
            raise ValueError("Loader program name cannot be empty.")
        safe_name = safe_program_name(program_name)
        if any(
            other.name == program_name
            or safe_program_name(other.name) == safe_name
            for other in self.loader_programs.values()
        ):
            raise ValueError(
                f"Loader program name '{program_name}' conflicts with an existing "
                "program or generated executable name."
            )
        graph_id = "loader_program_" + new_id("graph").split("_", 1)[1]
        graph = Graph(id=graph_id, name=program_name, scope="loader")
        self.loader_programs[graph.id] = graph
        return graph

    def rename_loader_program(self, program_id: str, new_name: str) -> Graph:
        graph = self.loader_programs[program_id]
        name = new_name.strip()
        if not name:
            raise ValueError("Loader program name cannot be empty.")
        safe_name = safe_program_name(name)
        if any(
            other.id != program_id
            and (other.name == name or safe_program_name(other.name) == safe_name)
            for other in self.loader_programs.values()
        ):
            raise ValueError(
                f"Loader program name '{name}' conflicts with an existing program "
                "or generated executable name."
            )
        graph.name = name
        return graph

    def remove_loader_program(self, program_id: str) -> list[str]:
        """Delete a Loader program and clear workflow references to it."""
        if program_id not in self.loader_programs:
            raise KeyError(program_id)
        cleared: list[str] = []
        for node in self.workflow.nodes:
            if (
                node.type == "loader_execute"
                and str(node.properties.get("loader_program", "")) == program_id
            ):
                node.properties["loader_program"] = ""
                cleared.append(node.id)
        del self.loader_programs[program_id]
        return cleared

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "Project":
        def graph_from_dict(raw: dict[str, object]) -> Graph:
            return Graph(
                id=str(raw["id"]),
                name=str(raw["name"]),
                scope=str(raw["scope"]),
                nodes=[WorkflowNode(**item) for item in raw.get("nodes", [])],
                edges=[WorkflowEdge(**item) for item in raw.get("edges", [])],
                root_order=[str(item) for item in raw.get("root_order", [])],
                foreach_regions=[
                    ForEachRegion(**item) for item in raw.get("foreach_regions", [])
                ],
            )

        workflow = graph_from_dict(data["workflow"])
        raw_programs = data.get("loader_programs", {})
        loader_programs = {
            key: graph_from_dict(value) for key, value in dict(raw_programs).items()
        }
        project = cls(
            name=str(data.get("name", "Untitled analysis")),
            workflow=workflow,
            loader_programs=loader_programs,
            backend=str(data.get("backend", "local")),
            backend_options=dict(data.get("backend_options", {})),
            build_options=dict(data.get("build_options", {})),
            version=int(data.get("version", 1)),
        )

        project._fill_current_defaults()
        project.version = 10
        # Region borders are the visual source of truth. Recompute immediate
        # parents on load so headless planning and the GUI interpret the same
        # nested layout.
        project.workflow.infer_region_hierarchy_from_geometry()
        for graph in [project.workflow, *project.loader_programs.values()]:
            graph.normalize_root_order()
        return project

    def _fill_current_defaults(self) -> None:
        from .registry import NODE_SPECS

        backend_defaults = {
            "local_workers": 4,
            "lsf_poll_seconds": 10,
            "lsf_max_active_jobs": 500,
            "lsf_cancel_on_failure": True,
            "condor_universe": "vanilla",
        }
        build_defaults = {
            "compiler": "g++",
            "cpp_standard": "c++17",
            "belle2_analysis_dir": "Belle2_analysis",
            "common_compile_flags": "",
            "common_link_flags": "",
            "loader_libraries": (
                "-lRooFit\n-lRooStats\n-lRooFitCore\n-lMinuit\n"
                "-lFastBDT_static"
            ),
        }

        for key, value in backend_defaults.items():
            self.backend_options.setdefault(key, value)

        for key, value in build_defaults.items():
            self.build_options.setdefault(key, value)

        for graph in [self.workflow, *self.loader_programs.values()]:
            for node in graph.nodes:
                spec = NODE_SPECS.get(node.type)
                if spec:
                    for prop in spec.properties:
                        node.properties.setdefault(prop.name, prop.default)

    def save(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: str | Path) -> "Project":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
