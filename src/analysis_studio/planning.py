from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import csv
import glob
import os
import re
from typing import Iterable

from .foreach_tokens import bind_tokens
from .model import (
    ForEachRegion,
    Graph,
    Project,
    WorkflowNode,
    custom_executable_path,
    loader_executable_path,
    new_id,
)
from .validation import validate_project, workflow_dependency_pairs


_TOKEN = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def expand_template(template: object, context: dict[str, object]) -> str:
    text = str(template or "")
    left_sentinel = "\x00ANALYSIS_STUDIO_LEFT_BRACE\x00"
    right_sentinel = "\x00ANALYSIS_STUDIO_RIGHT_BRACE\x00"
    text = text.replace("{{", left_sentinel).replace("}}", right_sentinel)

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in context:
            known = ", ".join(sorted(context)) or "(none)"
            raise ValueError(f"Unknown variable '{{{name}}}'. Available: {known}")
        return str(context[name])

    expanded = _TOKEN.sub(replace, text)
    return expanded.replace(left_sentinel, "{").replace(right_sentinel, "}")


def expand_path(template: object, context: dict[str, object]) -> str:
    return os.path.expandvars(os.path.expanduser(expand_template(template, context)))


def expand_argv(value: object, context: dict[str, object]) -> list[str]:
    return [
        expand_template(line, context)
        for line in str(value or "").splitlines()
        if line.strip()
    ]


@dataclass(frozen=True)
class PlanTask:
    id: str
    node_id: str
    title: str
    executable: str
    argv: tuple[str, ...]
    block_name: str
    mkdir_paths: tuple[str, ...]
    log_prefix: str
    log_suffix: str
    err_prefix: str
    err_suffix: str
    dependencies: tuple[str, ...] = ()
    concurrency_limits: dict[str, int] = field(default_factory=dict)
    lsf_queue: str = "s"

    @property
    def command(self) -> list[str]:
        return [self.executable, *self.argv]


@dataclass
class ExecutionPlan:
    tasks: list[PlanTask]

    def task(self, task_id: str) -> PlanTask:
        for task in self.tasks:
            if task.id == task_id:
                return task
        raise KeyError(task_id)

    def topological_order(self) -> list[PlanTask]:
        ids = {task.id for task in self.tasks}
        indegree = {task.id: 0 for task in self.tasks}
        children: dict[str, list[str]] = {task.id: [] for task in self.tasks}
        for task in self.tasks:
            for dependency in task.dependencies:
                if dependency not in ids:
                    raise ValueError(
                        f"Task '{task.title}' depends on missing task '{dependency}'."
                    )
                indegree[task.id] += 1
                children[dependency].append(task.id)
        ready = [task.id for task in self.tasks if indegree[task.id] == 0]
        result: list[PlanTask] = []
        while ready:
            task_id = ready.pop(0)
            result.append(self.task(task_id))
            for child in children[task_id]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)
        if len(result) != len(self.tasks):
            raise ValueError("The expanded execution plan contains a cycle.")
        return result


class PlanBuilder:
    def __init__(self, project: Project, project_directory: str | Path | None = None) -> None:
        self.project = project
        self.project_directory = Path(project_directory or ".").resolve()
        self.tasks: list[PlanTask] = []

    def build(self) -> ExecutionPlan:
        errors = validate_project(self.project, self.project_directory)
        if errors:
            raise ValueError("Project validation failed:\n" + "\n".join(errors))
        self._expand_scope(
            self.project.workflow,
            parent_region_id=None,
            context={},
            external_dependencies=[],
            inherited_limits={},
            label_prefix="",
        )
        plan = ExecutionPlan(self.tasks)
        plan.topological_order()
        return plan

    def _scope_node_ids(
        self, graph: Graph, parent_region_id: str | None
    ) -> list[str]:
        result: list[str] = []
        for node in graph.nodes:
            direct = graph.direct_region_for_node(node.id)
            direct_id = direct.id if direct else None
            if direct_id == parent_region_id:
                result.append(node.id)
        return result

    def _unit_for_node(
        self, graph: Graph, node_id: str, parent_region_id: str | None
    ) -> str | None:
        direct = graph.direct_region_for_node(node_id)
        direct_id = direct.id if direct else None
        if direct_id == parent_region_id:
            return node_id
        child = graph.immediate_child_region_for_node(node_id, parent_region_id)
        return child.id if child else None

    def _scope_units(
        self, graph: Graph, parent_region_id: str | None
    ) -> list[str]:
        direct_nodes = self._scope_node_ids(graph, parent_region_id)
        child_regions = [region.id for region in graph.child_regions(parent_region_id)]
        return [*direct_nodes, *child_regions]

    def _scope_pairs(
        self,
        graph: Graph,
        parent_region_id: str | None,
        all_pairs: list[tuple[str, str]],
    ) -> list[tuple[str, str]]:
        result: list[tuple[str, str]] = []
        for source, target in all_pairs:
            source_unit = self._unit_for_node(graph, source, parent_region_id)
            target_unit = self._unit_for_node(graph, target, parent_region_id)
            if (
                source_unit is not None
                and target_unit is not None
                and source_unit != target_unit
                and (source_unit, target_unit) not in result
            ):
                result.append((source_unit, target_unit))
        return result

    def _ordered_units(
        self,
        graph: Graph,
        parent_region_id: str | None,
        units: list[str],
        pairs: list[tuple[str, str]],
    ) -> list[str]:
        indegree = {unit: 0 for unit in units}
        children: dict[str, list[str]] = {unit: [] for unit in units}
        for source, target in pairs:
            indegree[target] += 1
            children[source].append(target)

        try:
            all_pairs = workflow_dependency_pairs(graph)
            extra_dependencies = [
                pair for pair in all_pairs if pair not in graph.dependency_pairs()
            ]
            roots = graph.ordered_roots(extra_dependencies)
        except ValueError:
            roots = graph.ordered_roots()

        root_position = {node.id: index for index, node in enumerate(roots)}
        fallback = len(root_position) + len(units)
        unit_rank: dict[str, int] = {unit: fallback for unit in units}
        for node_id, rank in root_position.items():
            unit = self._unit_for_node(graph, node_id, parent_region_id)
            if unit in unit_rank:
                unit_rank[unit] = min(unit_rank[unit], rank)
        insertion = {unit: index for index, unit in enumerate(units)}

        ready = [unit for unit in units if indegree[unit] == 0]
        ready.sort(key=lambda item: (unit_rank[item], insertion[item]))
        ordered: list[str] = []
        while ready:
            unit = ready.pop(0)
            ordered.append(unit)
            for child in children[unit]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)
            ready.sort(key=lambda item: (unit_rank[item], insertion[item]))
        if len(ordered) != len(units):
            scope = (
                graph.region(parent_region_id).title
                if parent_region_id is not None
                else graph.name
            )
            raise ValueError(f"{scope}: nested workflow units create a dependency cycle.")
        return ordered

    def _expand_scope(
        self,
        graph: Graph,
        parent_region_id: str | None,
        context: dict[str, object],
        external_dependencies: list[str],
        inherited_limits: dict[str, int],
        label_prefix: str,
    ) -> list[str]:
        all_pairs = workflow_dependency_pairs(graph)
        units = self._scope_units(graph, parent_region_id)
        if not units:
            return list(external_dependencies)
        unit_pairs = self._scope_pairs(graph, parent_region_id, all_pairs)
        ordered_units = self._ordered_units(
            graph, parent_region_id, units, unit_pairs
        )

        dependencies_by_unit: dict[str, list[str]] = {unit: [] for unit in units}
        children_by_unit: dict[str, list[str]] = {unit: [] for unit in units}
        for source, target in unit_pairs:
            dependencies_by_unit[target].append(source)
            children_by_unit[source].append(target)

        child_region_ids = {
            region.id for region in graph.child_regions(parent_region_id)
        }
        terminal_tasks_by_unit: dict[str, list[str]] = {}
        for unit in ordered_units:
            dependency_units = dependencies_by_unit[unit]
            if dependency_units:
                task_dependencies = _unique(
                    task_id
                    for dependency_unit in dependency_units
                    for task_id in terminal_tasks_by_unit[dependency_unit]
                )
            else:
                task_dependencies = list(external_dependencies)

            if unit in child_region_ids:
                terminal_tasks_by_unit[unit] = self._expand_region(
                    graph,
                    graph.region(unit),
                    context,
                    task_dependencies,
                    inherited_limits,
                    label_prefix,
                )
            else:
                terminal_tasks_by_unit[unit] = self._expand_single_node(
                    graph.node(unit),
                    context,
                    task_dependencies,
                    inherited_limits,
                    label_prefix,
                )

        terminal_units = [unit for unit in units if not children_by_unit[unit]]
        return _unique(
            task_id
            for unit in terminal_units
            for task_id in terminal_tasks_by_unit[unit]
        )

    def _expand_region(
        self,
        graph: Graph,
        region: ForEachRegion,
        context: dict[str, object],
        dependencies: list[str],
        inherited_limits: dict[str, int],
        label_prefix: str,
    ) -> list[str]:
        items = self._for_each_items(region, context)
        if not items:
            raise ValueError(f"{region.title}: For Each source produced no items.")

        limits = dict(inherited_limits)

        terminals: list[str] = []
        for index, raw_item in enumerate(items):
            raw_item["index"] = index
            iteration_context = dict(context)
            bound = bind_tokens(region.properties, raw_item)
            collisions = sorted(set(iteration_context).intersection(bound))
            if collisions:
                rendered = ", ".join(collisions)
                raise ValueError(
                    f"{region.title}: loop variable name(s) conflict with an outer "
                    f"For Each region: {rendered}."
                )
            iteration_context.update(bound)
            item_label = str(
                raw_item.get(
                    "filename",
                    raw_item.get("value", raw_item.get("index", index)),
                )
            )
            prefix = f"{label_prefix}{region.title}[{index}: {item_label}] / "
            terminals.extend(
                self._expand_scope(
                    graph,
                    parent_region_id=region.id,
                    context=iteration_context,
                    external_dependencies=dependencies,
                    inherited_limits=limits,
                    label_prefix=prefix,
                )
            )
        return _unique(terminals)

    def _expand_single_node(
        self,
        node: WorkflowNode,
        context: dict[str, object],
        dependencies: list[str],
        limits: dict[str, int],
        label_prefix: str,
    ) -> list[str]:
        if node.type in {"loader_execute", "custom_command"}:
            task = self._make_command_task(
                node, context, dependencies, limits, label_prefix
            )
            self.tasks.append(task)
            return [task.id]
        if node.type == "wait":
            if not dependencies:
                raise ValueError(
                    f"{node.title}: Wait has no dependency. Connect blocks or list "
                    "their names in 'Wait for'."
                )
            return list(dependencies)
        raise ValueError(f"{node.title}: unsupported workflow block '{node.type}'.")

    def _runtime_path(self, value: object, context: dict[str, object]) -> str:
        expanded = expand_path(value, context)
        if not expanded:
            return ""
        path = Path(expanded)
        if not path.is_absolute():
            path = self.project_directory / path
        return str(path.resolve())

    def _make_command_task(
        self,
        node: WorkflowNode,
        context: dict[str, object],
        dependencies: list[str],
        limits: dict[str, int],
        label_prefix: str,
    ) -> PlanTask:
        local_context = dict(context)
        mkdir_paths = tuple(
            self._runtime_path(line, local_context)
            for line in str(node.properties.get("mkdir_p", "")).splitlines()
            if line.strip()
        )
        if node.type == "loader_execute":
            program_id = str(node.properties.get("loader_program", ""))
            program = self.project.loader_programs.get(program_id)
            if program is None:
                raise ValueError(
                    f"{node.title}: Loader program '{program_id}' does not exist."
                )
            executable = self._runtime_path(loader_executable_path(program), local_context)
        else:
            executable = self._runtime_path(custom_executable_path(node, self.project), local_context)
        argv = tuple(expand_argv(node.properties.get("argv", ""), local_context))
        title = f"{label_prefix}{node.title}" if label_prefix else node.title
        return PlanTask(
            id=new_id("task"),
            node_id=node.id,
            title=title,
            executable=executable,
            argv=argv,
            block_name=node.title or "AnalysisStudio",
            mkdir_paths=mkdir_paths,
            log_prefix=expand_template(node.properties.get("log_prefix", ""), local_context),
            log_suffix=expand_template(node.properties.get("log_suffix", ""), local_context),
            err_prefix=expand_template(node.properties.get("err_prefix", ""), local_context),
            err_suffix=expand_template(node.properties.get("err_suffix", ""), local_context),
            dependencies=tuple(_unique(dependencies)),
            lsf_queue=str(node.properties.get("lsf_queue", "s")).strip() or "s",
        )

    def _for_each_items(
        self,
        region: ForEachRegion,
        context: dict[str, object],
    ) -> list[dict[str, object]]:
        mode = str(region.properties.get("source_mode", "root_files"))
        if mode == "root_files":
            directory = expand_path(region.properties.get("directory", ""), context)
            directory_path = Path(directory)
            if not directory_path.is_absolute():
                directory_path = self.project_directory / directory_path
            pattern = expand_template(region.properties.get("pattern", "*.root"), context)
            paths = sorted(glob.glob(str(directory_path / pattern)))
            return [
                {
                    "path": path,
                    "directory": str(Path(path).parent),
                    "filename": Path(path).name,
                    "stem": Path(path).stem,
                    "suffix": Path(path).suffix,
                }
                for path in paths
            ]

        if mode == "values":
            values = [
                expand_template(line.strip(), context)
                for line in str(region.properties.get("values", "")).splitlines()
                if line.strip()
            ]
            return [{"value": value} for value in values]

        if mode == "csv_rows":
            path = Path(expand_path(region.properties.get("csv_file", ""), context))
            if not path.is_absolute():
                path = self.project_directory / path
            delimiter = expand_template(
                region.properties.get("delimiter", ","), context
            )
            if delimiter == r"\t":
                delimiter = "\t"
            if len(delimiter) != 1:
                raise ValueError(f"{region.title}: CSV delimiter must be one character.")
            has_header = bool(region.properties.get("has_header", True))
            rows: list[dict[str, object]] = []
            with path.open("r", encoding="utf-8-sig", newline="") as stream:
                if has_header:
                    reader = csv.DictReader(stream, delimiter=delimiter)
                    for row in reader:
                        rows.append(
                            {
                                "csv_file": str(path),
                                "columns": {
                                    str(raw_name): "" if value is None else value
                                    for raw_name, value in row.items()
                                },
                            }
                        )
                else:
                    reader = csv.reader(stream, delimiter=delimiter)
                    for row in reader:
                        rows.append(
                            {
                                "csv_file": str(path),
                                "column_values": list(row),
                            }
                        )
            return rows

        raise ValueError(f"{region.title}: unsupported For Each source '{mode}'.")


def build_execution_plan(
    project: Project,
    project_directory: str | Path | None = None,
) -> ExecutionPlan:
    return PlanBuilder(project, project_directory).build()
