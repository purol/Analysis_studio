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
    # Kept only to read v0.1/v0.2 projects. Version 3 stores For Each bodies as
    # visible regions on the main workflow graph.
    foreach_graphs: dict[str, Graph] = field(default_factory=dict)
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
    version: int = 8

    @classmethod
    def empty(cls, name: str = "Untitled analysis") -> "Project":
        return cls(
            name=name,
            workflow=Graph(id="workflow", name="Workflow", scope="workflow"),
            loader_programs={},
        )

    @property
    def loader_graphs(self) -> dict[str, Graph]:
        return self.loader_programs

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
        # Do not perpetuate the old hidden-body representation in new files.
        data["foreach_graphs"] = {}
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
        raw_programs = data.get("loader_programs")
        if raw_programs is None:
            raw_programs = data.get("loader_graphs", {})
        loader_programs = {
            key: graph_from_dict(value) for key, value in dict(raw_programs).items()
        }
        foreach_graphs = {
            key: graph_from_dict(value)
            for key, value in dict(data.get("foreach_graphs", {})).items()
        }
        project = cls(
            name=str(data.get("name", "Untitled analysis")),
            workflow=workflow,
            loader_programs=loader_programs,
            foreach_graphs=foreach_graphs,
            backend=str(data.get("backend", "local")),
            backend_options=dict(data.get("backend_options", {})),
            build_options=dict(data.get("build_options", {})),
            version=int(data.get("version", 1)),
        )
        if project.version < 2:
            project._migrate_v1()
            project.version = 2
        if project.version < 3:
            project._migrate_v2()
            project.version = 3
        if project.version < 4:
            project._migrate_v3()
        if project.version < 5:
            project._migrate_v4()
        if project.version < 6:
            project._migrate_v5()
        if project.version < 7:
            project._migrate_v6()
        if project.version < 8:
            project._migrate_v7()
        project._fill_current_defaults()
        project.version = 8
        # Region borders are the visual source of truth. Recompute immediate
        # parents on load so headless planning and the GUI interpret the same
        # nested layout.
        project.workflow.infer_region_hierarchy_from_geometry()
        for graph in [project.workflow, *project.loader_programs.values()]:
            graph.normalize_root_order()
        return project

    def _migrate_v1(self) -> None:
        """Best-effort migration from the first public prototype to v0.2."""
        from .registry import NODE_SPECS

        def cpp_quote(value: object) -> str:
            text = str(value)
            return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'

        def cpp_vector(value: object) -> str:
            items = [part.strip() for part in re.split(r"[,\n]", str(value)) if part.strip()]
            return "{" + ", ".join(cpp_quote(item) for item in items) + "}"

        def argv_lines(template: object) -> str:
            text = str(template or "")
            text = text.replace("{input}", "{path}").replace("{input_dir}", "{directory}")
            try:
                return "\n".join(shlex.split(text))
            except ValueError:
                return text

        for graph in self.loader_programs.values():
            if any(node.type == "loader_decl" for node in graph.nodes):
                continue
            declaration = WorkflowNode(
                id=new_id("node"),
                type="loader_decl",
                title="Loader",
                x=min((node.x for node in graph.nodes), default=0.0) - 260.0,
                y=min((node.y for node in graph.nodes), default=0.0),
                properties=NODE_SPECS["loader_decl"].defaults(),
            )
            ending = WorkflowNode(
                id=new_id("node"),
                type="loader_end",
                title="End",
                x=max((node.x for node in graph.nodes), default=0.0) + 260.0,
                y=max((node.y for node in graph.nodes), default=0.0),
                properties=NODE_SPECS["loader_end"].defaults(),
            )
            ending.properties["loader_ref"] = declaration.id
            supported = {
                "load", "load_with_cut", "cut", "draw_th1d", "print_root",
                "bcs", "define_variable",
            }
            loader_name = str(declaration.properties["variable_name"])
            for node in graph.nodes:
                if node.type in supported:
                    node.properties["loader_ref"] = declaration.id
                    continue
                old_type = node.type
                old = dict(node.properties)
                if old_type == "save_root":
                    lines = [
                        f"{loader_name}.PrintSeparateRootFile(("
                        f"{old.get('path_cpp', 'std::string(\".\")')}).c_str(), "
                        f"{cpp_quote(old.get('prefix', ''))}, {cpp_quote(old.get('suffix', ''))});"
                    ]
                elif old_type == "set_samples":
                    lines = [
                        f"{loader_name}.SetMC({cpp_vector(old.get('mc', ''))});",
                        f"{loader_name}.SetData({cpp_vector(old.get('data', ''))});",
                        f"{loader_name}.SetSignal({cpp_vector(old.get('signal', ''))});",
                        f"{loader_name}.SetBackground({cpp_vector(old.get('background', ''))});",
                    ]
                elif old_type == "print_information":
                    lines = [f"{loader_name}.PrintInformation({cpp_quote(old.get('message', ''))});"]
                elif old_type == "raw_cpp":
                    continue
                else:
                    compact = json.dumps(old, ensure_ascii=False, sort_keys=True)
                    lines = [
                        f"// TODO: migrate legacy Analysis Studio v0.1 block '{old_type}'.",
                        f"// Original properties: {compact}",
                    ]
                node.type = "raw_cpp"
                node.properties = {"code": "\n".join(lines)}

            roots = [node for node in graph.nodes if not graph.incoming(node.id)]
            sinks = [node for node in graph.nodes if not graph.outgoing(node.id)]
            graph.nodes.insert(0, declaration)
            graph.nodes.append(ending)
            if not roots:
                graph.edges.append(WorkflowEdge(new_id("edge"), declaration.id, "out", ending.id, "in"))
            else:
                for root in roots:
                    graph.edges.append(WorkflowEdge(new_id("edge"), declaration.id, "out", root.id, "in"))
                for sink in sinks:
                    graph.edges.append(WorkflowEdge(new_id("edge"), sink.id, "out", ending.id, "in"))

        def convert_command(node: WorkflowNode, loader: bool) -> None:
            old = dict(node.properties)
            node.type = "loader_execute" if loader else "custom_command"
            node.properties = NODE_SPECS[node.type].defaults()
            node.properties.update({
                "executable": old.get("executable", ""),
                "argv": argv_lines(old.get("arguments", "")),
                "output_dir": old.get("output_dir", ""),
                "job_name": old.get("job_name", "Loader" if loader else "Command"),
            })
            if loader:
                node.properties["loader_program"] = old.get("pipeline", "")

        consumed_children: set[str] = set()
        for source in list(self.workflow.nodes):
            if source.type != "root_files":
                continue
            candidates = [
                self.workflow.node(edge.target)
                for edge in self.workflow.outgoing(source.id)
                if self.workflow.node(edge.target).type == "analysis_stage"
                and str(self.workflow.node(edge.target).properties.get("run_mode", "")) == "per_file"
            ]
            body_id = f"foreach_{source.id}"
            while body_id in self.foreach_graphs:
                body_id += "_new"
            body = Graph(id=body_id, name=f"{source.title} body", scope="workflow")
            if candidates:
                child = candidates[0]
                clone = WorkflowNode(
                    id=new_id("node"), type=child.type, title=child.title,
                    x=0.0, y=0.0, properties=dict(child.properties),
                )
                convert_command(clone, loader=True)
                body.nodes.append(clone)
                consumed_children.add(child.id)
                child_outgoing = list(self.workflow.outgoing(child.id))
                self.workflow.edges = [
                    edge for edge in self.workflow.edges
                    if edge.source != child.id and edge.target != child.id
                ]
                for edge in child_outgoing:
                    self.workflow.edges.append(
                        WorkflowEdge(new_id("edge"), source.id, "out", edge.target, edge.target_port)
                    )
            self.foreach_graphs[body.id] = body
            old_source = dict(source.properties)
            source.type = "for_each"
            source.properties = {
                    "source_mode": "root_files",
                    "body_graph": "",
                    "directory": "./Ntuple",
                    "pattern": "*.root",
                    "csv_file": "./items.csv",
                    "delimiter": ",",
                    "has_header": True,
                    "values": "value_a\nvalue_b",
                    "variable_name": "item",
                    "max_parallel": 0,
                }
            source.properties.update({
                "source_mode": "root_files",
                "body_graph": body.id,
                "directory": old_source.get("directory", "."),
                "pattern": old_source.get("pattern", "*.root"),
            })

        if consumed_children:
            self.workflow.nodes = [
                node for node in self.workflow.nodes if node.id not in consumed_children
            ]

        for node in self.workflow.nodes:
            if node.type == "analysis_stage":
                convert_command(node, loader=True)
            elif node.type in {"command_stage", "validator"}:
                convert_command(node, loader=False)
            elif node.type == "join":
                node.type = "wait"
                node.properties = NODE_SPECS["wait"].defaults()
            elif node.type == "root_files":
                body_id = f"foreach_{node.id}"
                body = Graph(id=body_id, name=f"{node.title} body", scope="workflow")
                self.foreach_graphs[body.id] = body
                old = dict(node.properties)
                node.type = "for_each"
                node.properties = {
                    "source_mode": "root_files",
                    "body_graph": "",
                    "directory": "./Ntuple",
                    "pattern": "*.root",
                    "csv_file": "./items.csv",
                    "delimiter": ",",
                    "has_header": True,
                    "values": "value_a\nvalue_b",
                    "variable_name": "item",
                    "max_parallel": 0,
                }
                node.properties.update({
                    "source_mode": "root_files",
                    "body_graph": body.id,
                    "directory": old.get("directory", "."),
                    "pattern": old.get("pattern", "*.root"),
                })
            elif node.type not in NODE_SPECS and node.type != "for_each":
                old_type = node.type
                old = json.dumps(node.properties, ensure_ascii=False, sort_keys=True)
                node.type = "custom_command"
                node.properties = NODE_SPECS["custom_command"].defaults()
                node.properties["executable"] = "/bin/echo"
                node.properties["argv"] = f"TODO: migrate legacy workflow block {old_type}\n{old}"

    def _migrate_v2(self) -> None:
        """Convert hidden For Each body tabs and explicit loader references to v3."""
        from .registry import FOREACH_DEFAULTS

        for graph in self.loader_programs.values():
            try:
                old_order = graph.topological_order()
            except ValueError:
                old_order = list(graph.nodes)
            declaration_order = [node.id for node in old_order if node.type == "loader_decl"]
            graph.edges = [
                edge for edge in graph.edges
                if graph.node(edge.target).type != "loader_decl"
                and graph.node(edge.source).type != "loader_end"
            ]
            for node in graph.nodes:
                node.properties.pop("loader_ref", None)
            graph.root_order = declaration_order
            graph.normalize_root_order()

        def flatten_for_each(graph: Graph) -> None:
            for node in list(graph.nodes):
                if node.type != "for_each":
                    continue
                body_id = str(node.properties.get("body_graph", ""))
                body = self.foreach_graphs.get(body_id, Graph(body_id or new_id("legacy"), "Legacy body", "workflow"))
                flatten_for_each(body)

                incoming = list(graph.incoming(node.id))
                outgoing = list(graph.outgoing(node.id))
                graph.edges = [
                    edge for edge in graph.edges
                    if edge.source != node.id and edge.target != node.id
                ]
                graph.nodes = [item for item in graph.nodes if item.id != node.id]

                min_x = min((item.x for item in body.nodes), default=0.0)
                min_y = min((item.y for item in body.nodes), default=0.0)
                offset_x = node.x + 36.0 - min_x
                offset_y = node.y + 54.0 - min_y
                existing_ids = {item.id for item in graph.nodes}
                id_map: dict[str, str] = {}
                for body_node in body.nodes:
                    new_node_id = body_node.id if body_node.id not in existing_ids else new_id("node")
                    id_map[body_node.id] = new_node_id
                    existing_ids.add(new_node_id)
                    graph.nodes.append(WorkflowNode(
                        id=new_node_id,
                        type=body_node.type,
                        title=body_node.title,
                        x=body_node.x + offset_x,
                        y=body_node.y + offset_y,
                        properties=dict(body_node.properties),
                    ))
                for edge in body.edges:
                    graph.edges.append(WorkflowEdge(
                        id=new_id("edge"),
                        source=id_map[edge.source], source_port=edge.source_port,
                        target=id_map[edge.target], target_port=edge.target_port,
                    ))

                body_roots = [item for item in body.nodes if not body.incoming(item.id)]
                body_sinks = [item for item in body.nodes if not body.outgoing(item.id)]
                for edge in incoming:
                    for root in body_roots:
                        graph.edges.append(WorkflowEdge(
                            new_id("edge"), edge.source, edge.source_port,
                            id_map[root.id], "in",
                        ))
                for sink in body_sinks:
                    for edge in outgoing:
                        graph.edges.append(WorkflowEdge(
                            new_id("edge"), id_map[sink.id], "out",
                            edge.target, edge.target_port,
                        ))

                # A v0.2 Wait could name the old For Each block. Replace that
                # hidden block reference with the visible terminal block(s).
                replacement_refs = [id_map[sink.id] for sink in body_sinks]
                for wait_node in graph.nodes:
                    if wait_node.type != "wait":
                        continue
                    references = [
                        part.strip()
                        for part in re.split(r"[,\n]", str(wait_node.properties.get("wait_for", "")))
                        if part.strip()
                    ]
                    changed = False
                    migrated: list[str] = []
                    for reference in references:
                        if reference in {node.id, node.title}:
                            migrated.extend(replacement_refs)
                            changed = True
                        else:
                            migrated.append(reference)
                    if changed:
                        wait_node.properties["wait_for"] = "\n".join(migrated)

                properties = dict(FOREACH_DEFAULTS)
                properties.update({
                    key: value for key, value in node.properties.items()
                    if key != "body_graph"
                })
                members = list(id_map.values())
                if members:
                    xs = [graph.node(member).x for member in members]
                    ys = [graph.node(member).y for member in members]
                    width = max(420.0, max(xs) - min(xs) + 270.0)
                    height = max(210.0, max(ys) - min(ys) + 160.0)
                else:
                    width, height = 520.0, 230.0
                graph.foreach_regions.append(ForEachRegion(
                    id=new_id("foreach"),
                    title=node.title,
                    x=node.x,
                    y=node.y,
                    width=width,
                    height=height,
                    properties=properties,
                    member_node_ids=members,
                ))

        flatten_for_each(self.workflow)
        self.foreach_graphs = {}
        self.workflow.normalize_root_order()

    def _migrate_v3(self) -> None:
        """Make every For Each variable explicit and remove hidden globals."""
        from .foreach_tokens import extract_tokens, sanitize_token_name

        # v0.3 exposed these implicit variables everywhere. Replace the global
        # ones with ordinary values or relative paths before restricting curly
        # braces to explicit For Each variables.
        for node in self.workflow.nodes:
            loader_program_id = str(node.properties.get("loader_program", ""))
            loader_program = self.loader_programs.get(loader_program_id)
            global_replacements = {
                "project_dir": ".",
                "project_name": self.name,
                "loader_program": loader_program_id,
                "loader_program_id": loader_program_id,
                "loader_program_name": loader_program.name if loader_program else "",
                "loader_source": (
                    re.sub(r"[^A-Za-z0-9_.-]+", "_", loader_program.name).strip("_") + ".cc"
                    if loader_program else ""
                ),
                "output_dir": str(node.properties.get("output_dir", "")),
            }
            for key, value in list(node.properties.items()):
                if not isinstance(value, str):
                    continue
                migrated = value
                for token, replacement in global_replacements.items():
                    migrated = migrated.replace("{" + token + "}", replacement)
                node.properties[key] = migrated

        for region in self.workflow.foreach_regions:
            mode = str(region.properties.get("source_mode", "root_files"))
            member_nodes = [
                self.workflow.node(node_id)
                for node_id in region.member_node_ids
                if any(node.id == node_id for node in self.workflow.nodes)
            ]
            used: list[str] = []
            legacy_pattern = re.compile(r"\{([A-Za-z_][A-Za-z0-9_.-]*)\}")
            for node in member_nodes:
                for value in node.properties.values():
                    if not isinstance(value, str):
                        continue
                    for match in legacy_pattern.finditer(value):
                        token = match.group(1)
                        if token not in used:
                            used.append(token)

            bindings: list[dict[str, str]] = []
            replacements: dict[str, str] = {}
            allocated: dict[str, str] = {}
            old_value_name = sanitize_token_name(
                str(region.properties.get("variable_name", "item")), "item"
            )

            for old_name in used:
                source = ""
                requested_name = old_name
                if mode == "root_files" and old_name in {
                    "path", "directory", "filename", "stem", "suffix", "index"
                }:
                    source = old_name
                elif mode == "values":
                    if old_name in {"item", old_value_name}:
                        source = "value"
                        requested_name = old_value_name
                    elif old_name == "index":
                        source = "index"
                elif mode == "csv_rows":
                    if old_name == "index":
                        source = "index"
                    elif old_name == "item":
                        source = "csv_file"
                    elif old_name.startswith("csv."):
                        requested_name = old_name.split(".", 1)[1]
                        requested_name = sanitize_token_name(requested_name, "column")
                        source = f"column_sanitized:{requested_name}"
                    else:
                        requested_name = sanitize_token_name(old_name, "column")
                        source = f"column_sanitized:{requested_name}"

                # Unknown old placeholders stay undefined so validation points
                # them out instead of silently guessing their meaning.
                if not source:
                    continue

                new_name = sanitize_token_name(requested_name, "variable")
                if new_name in allocated and allocated[new_name] != source:
                    suffix = 2
                    candidate = f"{new_name}_{suffix}"
                    while candidate in allocated:
                        suffix += 1
                        candidate = f"{new_name}_{suffix}"
                    new_name = candidate
                if new_name not in allocated:
                    allocated[new_name] = source
                    bindings.append({"name": new_name, "source": source})
                replacements[old_name] = new_name

            for node in member_nodes:
                for key, value in list(node.properties.items()):
                    if not isinstance(value, str):
                        continue
                    migrated = value
                    for old_name, new_name in replacements.items():
                        migrated = migrated.replace(
                            "{" + old_name + "}", "{" + new_name + "}"
                        )
                    node.properties[key] = migrated

            region.properties["tokens"] = bindings
            region.properties.pop("variable_name", None)

    def _migrate_v4(self) -> None:
        """Infer nested region parents for v4 canvases and keep direct members."""
        self.workflow.infer_region_hierarchy_from_geometry()
        self.version = 5

    def _migrate_v5(self) -> None:
        """Remove the duplicated Loader Execute executable setting."""
        for node in self.workflow.nodes:
            if node.type == "loader_execute":
                node.properties.pop("executable", None)
        self.version = 6


    def _migrate_v6(self) -> None:
        """Rename Custom Command executable to portable project-local code."""
        from .registry import NODE_SPECS

        for node in self.workflow.nodes:
            spec = NODE_SPECS.get(node.type)
            if node.type == "custom_command":
                old = str(node.properties.pop("executable", "")).strip()
                if "code" not in node.properties:
                    node.properties["code"] = old or "code/my_command.py"
                if "output_name" not in node.properties:
                    node.properties["output_name"] = safe_program_name(
                        Path(str(node.properties["code"])).stem, "my_command"
                    )
            if spec:
                for prop in spec.properties:
                    node.properties.setdefault(prop.name, prop.default)
        self.version = 7

    def _migrate_v7(self) -> None:
        """Use global worker/job limits and derive Custom Command artifact names."""
        queue_aliases = {"short": "s", "long": "l", "huge": "h"}
        old_default_queue = str(self.backend_options.pop("lsf_queue", "s")).strip()
        old_default_queue = queue_aliases.get(old_default_queue, old_default_queue)
        old_limit = self.backend_options.pop("lsf_max_inflight", None)
        if "lsf_max_active_jobs" not in self.backend_options and old_limit is not None:
            self.backend_options["lsf_max_active_jobs"] = old_limit

        for node in self.workflow.nodes:
            if node.type in {"loader_execute", "custom_command"}:
                queue = str(node.properties.get("lsf_queue", "")).strip() or old_default_queue
                queue = queue_aliases.get(queue, queue)
                node.properties["lsf_queue"] = queue if queue in {"s", "l", "h"} else "s"
                for obsolete in (
                    "local_max_parallel",
                    "lsf_max_inflight",
                    "lsf_extra_options",
                ):
                    node.properties.pop(obsolete, None)
            if node.type == "custom_command":
                node.properties.pop("output_name", None)
                node.properties.pop("use_analysis_framework", None)
                if str(node.properties.get("build_mode", "auto")) == "copy":
                    node.properties["build_mode"] = "auto"
        for region in self.workflow.foreach_regions:
            region.properties.pop("max_parallel", None)
        self.version = 8

    def _fill_current_defaults(self) -> None:
        """Fill settings introduced by newer versions without discarding values."""
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
        allowed_backend_keys = set(backend_defaults)
        self.backend_options = {
            key: value
            for key, value in self.backend_options.items()
            if key in allowed_backend_keys
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
                if node.type in {"loader_execute", "custom_command"}:
                    for obsolete in ("local_max_parallel", "lsf_max_inflight", "lsf_extra_options"):
                        node.properties.pop(obsolete, None)
                if node.type == "custom_command":
                    node.properties.pop("output_name", None)
                    node.properties.pop("use_analysis_framework", None)
                    if str(node.properties.get("build_mode", "auto")) == "copy":
                        node.properties["build_mode"] = "auto"
            for region in graph.foreach_regions:
                region.properties.pop("max_parallel", None)

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
