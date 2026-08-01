from __future__ import annotations

from collections import Counter
from pathlib import Path
import re

from .foreach_tokens import (
    TOKEN_NAME_PATTERN,
    extract_tokens,
    token_bindings,
    valid_source_key,
)
from .model import (
    ForEachRegion, Graph, Project, WorkflowNode, custom_command_output_names,
)
from .registry import NODE_SPECS


_CPP_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def split_references(value: object) -> list[str]:
    text = str(value or "")
    return [part.strip() for part in re.split(r"[,\n]", text) if part.strip()]


def resolve_node_reference(graph: Graph, reference: str) -> WorkflowNode:
    try:
        return graph.node(reference)
    except KeyError:
        pass
    matches = [node for node in graph.nodes if node.title == reference]
    if not matches:
        raise ValueError(f"Unknown block name or ID '{reference}'.")
    if len(matches) > 1:
        raise ValueError(
            f"Block name '{reference}' is ambiguous; rename the blocks or use a node ID."
        )
    return matches[0]


def workflow_dependency_pairs(graph: Graph) -> list[tuple[str, str]]:
    pairs = graph.dependency_pairs()
    for node in graph.nodes:
        if node.type != "wait":
            continue
        for reference in split_references(node.properties.get("wait_for", "")):
            dependency = resolve_node_reference(graph, reference)
            if dependency.id == node.id:
                raise ValueError(f"{node.title}: a Wait block cannot wait for itself.")
            pairs.append((dependency.id, node.id))
    return pairs


def _undirected_components(graph: Graph) -> list[list[WorkflowNode]]:
    neighbors: dict[str, set[str]] = {node.id: set() for node in graph.nodes}
    for edge in graph.edges:
        neighbors[edge.source].add(edge.target)
        neighbors[edge.target].add(edge.source)
    result: list[list[WorkflowNode]] = []
    remaining = set(neighbors)
    while remaining:
        seed = next(iter(remaining))
        stack = [seed]
        component_ids: set[str] = set()
        while stack:
            current = stack.pop()
            if current in component_ids:
                continue
            component_ids.add(current)
            stack.extend(neighbors[current])
        remaining -= component_ids
        result.append([node for node in graph.nodes if node.id in component_ids])
    return result


def loader_declaration_for_node(graph: Graph, node: WorkflowNode) -> WorkflowNode:
    candidates = [
        declaration
        for declaration in graph.nodes
        if declaration.type == "loader_decl" and graph.has_path(declaration.id, node.id)
    ]
    if len(candidates) != 1:
        raise ValueError(
            f"{node.title}: expected exactly one connected Loader Declaration; "
            f"found {len(candidates)}."
        )
    return candidates[0]


def validate_loader_graph(graph: Graph) -> list[str]:
    errors = graph.validate(NODE_SPECS)
    if graph.scope != "loader":
        return [*errors, f"{graph.name}: expected a Loader program graph."]
    if not graph.nodes:
        return [*errors, f"{graph.name}: the Loader program is empty."]

    declarations = [node for node in graph.nodes if node.type == "loader_decl"]
    if not declarations:
        errors.append(f"{graph.name}: add at least one Loader Declaration block.")
        return errors

    variables: list[str] = []
    for declaration in declarations:
        variable = str(declaration.properties.get("variable_name", "")).strip()
        branch = str(declaration.properties.get("branch", "")).strip()
        if not _CPP_IDENTIFIER.fullmatch(variable):
            errors.append(
                f"{declaration.title}: '{variable}' is not a valid C++ variable name."
            )
        variables.append(variable)
        if not branch:
            errors.append(f"{declaration.title}: tree / branch name is empty.")
        if graph.incoming(declaration.id):
            errors.append(f"{declaration.title}: Loader Declaration must be a start node.")

    for variable, count in Counter(variables).items():
        if variable and count > 1:
            errors.append(f"{graph.name}: Loader variable '{variable}' is duplicated.")

    for component in _undirected_components(graph):
        component_ids = {node.id for node in component}
        component_declarations = [node for node in component if node.type == "loader_decl"]
        endings = [node for node in component if node.type == "loader_end"]
        label = component_declarations[0].title if component_declarations else component[0].title
        if len(component_declarations) != 1:
            errors.append(
                f"{label}: each Loader chain needs exactly one Loader Declaration; "
                f"found {len(component_declarations)}."
            )
        if len(endings) != 1:
            errors.append(
                f"{label}: each Loader chain needs exactly one End; found {len(endings)}."
            )
        if len(component_declarations) == 1 and len(endings) == 1:
            declaration = component_declarations[0]
            ending = endings[0]
            if graph.outgoing(ending.id):
                errors.append(f"{ending.title}: End must be the final node of its chain.")
            for node in component:
                if not graph.has_path(declaration.id, node.id):
                    errors.append(
                        f"{node.title}: connect it after {declaration.title}."
                    )
                if not graph.has_path(node.id, ending.id):
                    errors.append(f"{node.title}: connect it before {ending.title}.")
            roots = [
                node for node in component
                if not any(edge.target == node.id and edge.source in component_ids for edge in graph.edges)
            ]
            sinks = [
                node for node in component
                if not any(edge.source == node.id and edge.target in component_ids for edge in graph.edges)
            ]
            if roots != [declaration]:
                errors.append(f"{label}: Loader Declaration must be the only chain start.")
            if sinks != [ending]:
                errors.append(f"{label}: End must be the only chain end.")

    non_declaration_roots = [node for node in graph.roots() if node.type != "loader_decl"]
    for node in non_declaration_roots:
        errors.append(f"{node.title}: every Loader graph line must start with a declaration.")

    graph.normalize_root_order()
    if set(graph.root_order) != {node.id for node in declarations}:
        errors.append(
            f"{graph.name}: start order must contain every Loader Declaration exactly once."
        )

    try:
        graph.topological_order()
    except ValueError as exc:
        errors.append(str(exc))
    return errors


def _region_variable_names(region: ForEachRegion) -> list[str]:
    return [binding["name"] for binding in token_bindings(region.properties) if binding["name"]]


def _ancestor_variable_names(graph: Graph, region: ForEachRegion) -> list[str]:
    result: list[str] = []
    for ancestor in graph.ancestor_regions(region.id):
        for name in _region_variable_names(ancestor):
            if name not in result:
                result.append(name)
    return result


def _validate_region(region: ForEachRegion, graph: Graph) -> list[str]:
    errors: list[str] = []
    mode = str(region.properties.get("source_mode", ""))
    if mode not in {"root_files", "csv_rows", "values"}:
        errors.append(f"{region.title}: unknown For Each source '{mode}'.")
    if mode == "root_files" and not str(region.properties.get("directory", "")):
        errors.append(f"{region.title}: ROOT directory is empty.")
    if mode == "csv_rows" and not str(region.properties.get("csv_file", "")):
        errors.append(f"{region.title}: CSV file is empty.")
    if mode == "values" and not split_references(region.properties.get("values", "")):
        errors.append(f"{region.title}: Values list is empty.")

    try:
        ancestors = graph.ancestor_regions(region.id)
    except (KeyError, ValueError) as exc:
        errors.append(f"{region.title}: {exc}")
        ancestors = []
    ancestor_names = [
        name
        for ancestor in ancestors
        for name in _region_variable_names(ancestor)
    ]
    ancestor_name_set = set(ancestor_names)

    bindings = token_bindings(region.properties)
    names: list[str] = []
    for binding in bindings:
        name = binding["name"]
        source = binding["source"]
        if not TOKEN_NAME_PATTERN.fullmatch(name):
            errors.append(
                f"{region.title}: '{name}' is not a valid variable name. Use "
                "letters, numbers, and underscores, and do not start with a number."
            )
        if name in names:
            errors.append(f"{region.title}: variable '{name}' is defined more than once.")
        if name in ancestor_name_set:
            outer = next(
                ancestor.title
                for ancestor in ancestors
                if name in _region_variable_names(ancestor)
            )
            errors.append(
                f"{region.title}: variable '{name}' conflicts with the same variable "
                f"defined by outer region '{outer}'. Rename one of them."
            )
        names.append(name)
        if not source:
            errors.append(f"{region.title}: variable '{name}' has no source value.")
        elif not valid_source_key(mode, source):
            errors.append(
                f"{region.title}: variable '{name}' uses source '{source}', which "
                f"is not available for source mode '{mode}'."
            )

    # A nested source may depend on variables explicitly defined by outer loops,
    # but it cannot use variables created by itself or by an unrelated region.
    source_property_names = {
        "root_files": ("directory", "pattern"),
        "csv_rows": ("csv_file", "delimiter"),
        "values": ("values",),
    }.get(mode, ())
    for property_name in source_property_names:
        used = extract_tokens(region.properties.get(property_name, ""))
        unknown = [name for name in used if name not in ancestor_name_set]
        if unknown:
            rendered = ", ".join("{" + name + "}" for name in unknown)
            available = (
                ", ".join("{" + name + "}" for name in sorted(ancestor_name_set))
                or "none"
            )
            errors.append(
                f"{region.title}: source property '{property_name}' may use only "
                f"variables from outer For Each regions; invalid {rendered}. "
                f"Available outer variables: {available}."
            )

    child_regions = graph.child_regions(region.id)
    if not region.member_node_ids and not child_regions:
        errors.append(
            f"{region.title}: place at least one workflow block or nested For Each "
            "region inside the region."
        )

    subtree_ids = graph.region_subtree_node_ids(region.id)
    for node_id in region.member_node_ids:
        node = graph.node(node_id)
        if node.type == "wait":
            for reference in split_references(node.properties.get("wait_for", "")):
                try:
                    dependency = resolve_node_reference(graph, reference)
                except ValueError as exc:
                    errors.append(f"{node.title}: {exc}")
                    continue
                if dependency.id not in subtree_ids:
                    errors.append(
                        f"{node.title}: a Wait inside For Each may only name blocks "
                        "inside the same region or one of its nested regions. Use an "
                        "incoming line for outside dependencies."
                    )
    return errors


def _unit_for_node(
    graph: Graph, node_id: str, parent_region_id: str | None
) -> str | None:
    direct = graph.direct_region_for_node(node_id)
    direct_id = direct.id if direct else None
    if direct_id == parent_region_id:
        return node_id
    child = graph.immediate_child_region_for_node(node_id, parent_region_id)
    return child.id if child else None


def _validate_scope_cycles(
    graph: Graph,
    parent_region_id: str | None,
    pairs: list[tuple[str, str]],
) -> list[str]:
    errors: list[str] = []
    direct_nodes = [
        node.id
        for node in graph.nodes
        if (graph.direct_region_for_node(node.id).id if graph.direct_region_for_node(node.id) else None)
        == parent_region_id
    ]
    child_regions = graph.child_regions(parent_region_id)
    units = [*direct_nodes, *(region.id for region in child_regions)]
    unit_pairs = {
        (source_unit, target_unit)
        for source, target in pairs
        for source_unit in [_unit_for_node(graph, source, parent_region_id)]
        for target_unit in [_unit_for_node(graph, target, parent_region_id)]
        if source_unit is not None
        and target_unit is not None
        and source_unit != target_unit
    }
    indegree = {unit: 0 for unit in units}
    children = {unit: [] for unit in units}
    for source, target in unit_pairs:
        if source not in indegree or target not in indegree:
            continue
        indegree[target] += 1
        children[source].append(target)
    ready = [unit for unit in units if indegree[unit] == 0]
    visited = 0
    while ready:
        unit = ready.pop()
        visited += 1
        for child in children[unit]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    if visited != len(units):
        scope = graph.region(parent_region_id).title if parent_region_id else graph.name
        errors.append(
            f"{scope}: collapsing nested For Each regions creates a dependency "
            "cycle. Move the crossing block or change the lines."
        )
    for child in child_regions:
        errors.extend(_validate_scope_cycles(graph, child.id, pairs))
    return errors


RUNTIME_TOKEN_PROPERTIES = {
    "loader_execute": {
        "argv", "mkdir_p", "log_prefix", "log_suffix", "err_prefix", "err_suffix"
    },
    "custom_command": {
        "argv", "mkdir_p", "log_prefix", "log_suffix", "err_prefix", "err_suffix"
    },
}

BUILD_TIME_CUSTOM_PROPERTIES = {
    "code",
    "compile_command",
    "additional_sources",
    "compile_flags",
    "link_flags",
}


def validate_workflow_graph(
    graph: Graph, project: Project, project_directory: str | Path | None = None
) -> list[str]:
    errors = graph.validate(NODE_SPECS)
    if graph.scope != "workflow":
        return [*errors, f"{graph.name}: expected a workflow graph."]

    for node in graph.nodes:
        if node.type == "loader_execute":
            program_id = str(node.properties.get("loader_program", ""))
            if program_id not in project.loader_programs:
                errors.append(
                    f"{node.title}: Loader program '{program_id}' does not exist."
                )
        elif node.type == "custom_command":
            code = str(node.properties.get("code", "")).strip()
            if not code:
                errors.append(f"{node.title}: Code / script is empty.")
            else:
                code_path = Path(code)
                if code_path.is_absolute():
                    errors.append(
                        f"{node.title}: Code / script must be relative to the project "
                        f"directory for portability: {code}"
                    )
                elif ".." in code_path.parts:
                    errors.append(
                        f"{node.title}: Code / script may not escape the project "
                        f"directory: {code}"
                    )
                elif project_directory is not None:
                    root = Path(project_directory).resolve()
                    resolved = (root / code_path).resolve()
                    try:
                        resolved.relative_to(root)
                    except ValueError:
                        errors.append(
                            f"{node.title}: Code / script escapes the project directory: {code}"
                        )
                    else:
                        if not resolved.exists():
                            errors.append(f"{node.title}: Code / script does not exist: {code}")
            mode = str(node.properties.get("build_mode", "auto"))
            if mode not in {"auto", "custom"}:
                errors.append(f"{node.title}: unknown build mode '{mode}'.")
            if mode == "custom" and not str(node.properties.get("compile_command", "")).strip():
                errors.append(f"{node.title}: custom build mode needs a compile command.")
            if mode == "custom":
                for source_value in str(node.properties.get("additional_sources", "")).splitlines():
                    source_value = source_value.strip()
                    if not source_value:
                        continue
                    source_path = Path(source_value)
                    if source_path.is_absolute() or ".." in source_path.parts:
                        errors.append(
                            f"{node.title}: Additional C/C++ source must stay inside the "
                            f"project directory: {source_value}"
                        )
            build_time_properties = {"code"}
            if mode == "custom":
                build_time_properties.update(BUILD_TIME_CUSTOM_PROPERTIES - {"code"})
            for property_name in build_time_properties:
                used_at_build_time = extract_tokens(node.properties.get(property_name, ""))
                if used_at_build_time:
                    rendered = ", ".join("{" + name + "}" for name in used_at_build_time)
                    errors.append(
                        f"{node.title}: For Each variables cannot be used in build-time "
                        f"property '{property_name}': {rendered}. Build each program once "
                        "and pass loop-dependent values through argv or runtime paths."
                    )
        elif node.type not in {"wait"}:
            errors.append(f"{node.title}: unsupported workflow block '{node.type}'.")

        if node.type in {"loader_execute", "custom_command"}:
            queue = str(node.properties.get("lsf_queue", "s"))
            if queue not in {"s", "l", "h"}:
                errors.append(
                    f"{node.title}: LSF queue must be one of s, l, h; got '{queue}'."
                )
            for property_name in ("log_prefix", "log_suffix", "err_prefix", "err_suffix"):
                fragment = str(node.properties.get(property_name, ""))
                if "/" in fragment or "\\" in fragment:
                    errors.append(
                        f"{node.title}: {property_name} is a filename fragment and may "
                        "not contain a directory separator."
                    )

    for region in graph.foreach_regions:
        errors.extend(_validate_region(region, graph))

    for node in graph.nodes:
        used: list[str] = []
        token_properties = RUNTIME_TOKEN_PROPERTIES.get(node.type, set())
        for property_name in token_properties:
            value = node.properties.get(property_name, "")
            used.extend(name for name in extract_tokens(value) if name not in used)
        if not used:
            continue
        try:
            regions = graph.regions_for_node(node.id)
        except ValueError as exc:
            errors.append(f"{node.title}: {exc}")
            continue
        if not regions:
            rendered = ", ".join("{" + name + "}" for name in used)
            errors.append(
                f"{node.title}: For Each variables may be used only inside a "
                f"For Each region; found {rendered}."
            )
            continue
        available = {
            binding["name"]
            for region in regions
            for binding in token_bindings(region.properties)
            if binding["name"]
        }
        unknown = [name for name in used if name not in available]
        if unknown:
            rendered = ", ".join("{" + name + "}" for name in unknown)
            known = ", ".join("{" + name + "}" for name in sorted(available)) or "none"
            chain = " → ".join(region.title for region in regions)
            errors.append(
                f"{node.title}: undefined For Each variable(s) {rendered}. "
                f"Variables available from '{chain}': {known}."
            )

    output_names = custom_command_output_names(project)
    build_signatures: dict[str, tuple[object, ...]] = {}
    build_owners: dict[str, str] = {}
    for node in graph.nodes:
        if node.type != "custom_command":
            continue
        artifact = output_names[node.id]
        mode = str(node.properties.get("build_mode", "auto"))
        signature: tuple[object, ...] = (
            str(node.properties.get("code", "")),
            mode,
            *(
                (
                    str(node.properties.get("compile_command", "")),
                    str(node.properties.get("additional_sources", "")),
                    str(node.properties.get("compile_flags", "")),
                    str(node.properties.get("link_flags", "")),
                )
                if mode == "custom"
                else ()
            ),
        )
        owner_signature = build_signatures.get(artifact)
        if owner_signature is not None and owner_signature != signature:
            errors.append(
                f"{node.title}: bin/{artifact} is also used by {build_owners[artifact]} "
                "with different build settings. Blocks using the same Code / script "
                "must use identical build settings."
            )
        elif owner_signature is None:
            build_signatures[artifact] = signature
            build_owners[artifact] = f"Custom Command '{node.title}'"

    try:
        pairs = workflow_dependency_pairs(graph)
        extra = [pair for pair in pairs if pair not in graph.dependency_pairs()]
        graph.topological_order(extra)
        errors.extend(_validate_scope_cycles(graph, None, pairs))
    except ValueError as exc:
        errors.append(f"{graph.name}: {exc}")
    return errors


def validate_project(
    project: Project, project_directory: str | Path | None = None
) -> list[str]:
    errors: list[str] = []
    errors.extend(validate_workflow_graph(project.workflow, project, project_directory))
    referenced_programs = {
        str(node.properties.get("loader_program", ""))
        for node in project.workflow.nodes
        if node.type == "loader_execute"
    }
    for graph in project.loader_programs.values():
        if graph.nodes or graph.id in referenced_programs:
            errors.extend(validate_loader_graph(graph))
    return errors
