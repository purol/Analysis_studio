from __future__ import annotations

from pathlib import Path
import re
import shlex

from .model import Graph, Project, WorkflowNode


def cpp_string(value: object) -> str:
    text = str(value)
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def safe_filename(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return result or "pipeline"


def cpp_string_vector(value: object) -> str:
    labels = [
        item.strip()
        for item in re.split(r"[,\n]", str(value))
        if item.strip()
    ]
    return "{" + ", ".join(cpp_string(item) for item in labels) + "}"


def loader_node_to_cpp(node: WorkflowNode) -> list[str]:
    p = node.properties
    if node.type == "load":
        return [
            f"loader.Load({p['directory_cpp']}, {p['including_cpp']}, "
            f"{cpp_string(p['label'])});"
        ]
    if node.type == "load_with_cut":
        return [
            f"loader.LoadWithCut({p['directory_cpp']}, {p['including_cpp']}, "
            f"{cpp_string(p['label'])}, {cpp_string(p['condition'])});"
        ]
    if node.type == "define_variable":
        return [
            f"loader.DefineNewVariable({cpp_string(p['equation'])}, "
            f"{cpp_string(p['name'])});"
        ]
    if node.type == "conditional_pair_variable":
        return [
            f"loader.ConditionalPairDefineNewVariable({p['map_cpp']}, "
            f"{int(p['order'])}, {cpp_string(p['name'])});"
        ]
    if node.type == "aggregate_variable":
        variables = cpp_string_vector(p["expressions"])
        operation = str(p["operation"])
        if operation == "average":
            return [f"loader.GetAverage({variables}, {cpp_string(p['name'])});"]
        if operation == "stddev":
            return [f"loader.GetStdDev({variables}, {cpp_string(p['name'])});"]
        if operation == "diff":
            return [
                f"loader.GetDiff({variables}, {int(p['order'])}, "
                f"{cpp_string(p['name'])});"
            ]
        if operation == "add":
            return [
                f"loader.GetAdd({variables}, {int(p['order'])}, "
                f"{cpp_string(p['name'])});"
            ]
        raise ValueError(f"Unknown aggregate operation '{operation}'.")
    if node.type == "set_samples":
        return [
            f"loader.SetMC({cpp_string_vector(p['mc'])});",
            f"loader.SetData({cpp_string_vector(p['data'])});",
            f"loader.SetSignal({cpp_string_vector(p['signal'])});",
            f"loader.SetBackground({cpp_string_vector(p['background'])});",
        ]
    if node.type == "cut":
        return [f"loader.Cut({cpp_string(p['condition'])});"]
    if node.type == "print_information":
        return [f"loader.PrintInformation({cpp_string(p['message'])});"]
    if node.type == "save_root":
        return [
            f"loader.PrintSeparateRootFile(({p['path_cpp']}).c_str(), "
            f"{cpp_string(p['prefix'])}, {cpp_string(p['suffix'])});"
        ]
    if node.type == "draw_th1d":
        return [
            "loader.DrawTH1D("
            f"{cpp_string(p['expression'])}, {cpp_string(p['title'])}, "
            f"{int(p['bins'])}, {float(p['minimum'])}, {float(p['maximum'])}, "
            f"{cpp_string(p['filename'])});"
        ]
    if node.type == "draw_th2d":
        return [
            "loader.DrawTH2D("
            f"{cpp_string(p['x_expression'])}, {cpp_string(p['y_expression'])}, "
            f"{cpp_string(p['title'])}, "
            f"{int(p['x_bins'])}, {float(p['x_minimum'])}, {float(p['x_maximum'])}, "
            f"{int(p['y_bins'])}, {float(p['y_minimum'])}, {float(p['y_maximum'])}, "
            f"{cpp_string(p['filename'])}, {cpp_string(p['draw_option'])});"
        ]
    if node.type == "draw_stack":
        normalized = "true" if bool(p["normalized"]) else "false"
        log_scale = "true" if bool(p["log_scale"]) else "false"
        return [
            "loader.DrawStack("
            f"{cpp_string(p['expression'])}, {cpp_string(p['title'])}, "
            f"{int(p['bins'])}, {float(p['minimum'])}, {float(p['maximum'])}, "
            f"{cpp_string(p['filename'])}, {normalized}, {log_scale});"
        ]
    if node.type == "print_root":
        return [f"loader.PrintRootFile({cpp_string(p['filename'])});"]
    if node.type == "bcs":
        return [
            f"loader.BCS({cpp_string(p['expression'])}, {cpp_string(p['criteria'])});"
        ]
    if node.type == "fastbdt_apply":
        return [
            "loader.FastBDTApplication("
            f"{cpp_string_vector(p['variables'])}, {cpp_string(p['classifier'])}, "
            f"{cpp_string(p['branch'])});"
        ]
    if node.type == "raw_cpp":
        return str(p["code"]).splitlines()
    raise ValueError(f"No C++ generator exists for loader block '{node.type}'.")


def generate_loader_cpp(graph: Graph) -> str:
    errors = []
    if graph.scope != "loader":
        errors.append("Only Loader graphs can generate C++.")
    if errors:
        raise ValueError("\n".join(errors))

    statements: list[str] = []
    for node in graph.topological_order():
        statements.append(f"    // {node.title}")
        statements.extend(f"    {line}" if line else "" for line in loader_node_to_cpp(node))
        statements.append("")

    body = "\n".join(statements).rstrip()
    return f"""// Generated by BelleFlow Studio. Review before production use.
#include <stdio.h>
#include <string>
#include <vector>
#include <map>

#include "TFile.h"
#include "Loader.h"

int main(int argc, char* argv[]) {{
    if (argc < 4) {{
        fprintf(stderr, "Usage: %s <input-dir> <including-string> <output-dir>\\n", argv[0]);
        return 2;
    }}

    Loader loader("tau_lfv");

{body}

    loader.end();
    return 0;
}}
"""


def command_preview(node: WorkflowNode) -> str:
    if node.type in {"analysis_stage", "command_stage", "validator"}:
        executable = shlex.quote(str(node.properties.get("executable", "")))
        arguments = str(node.properties.get("arguments", ""))
        return f"{executable} {arguments}".strip()
    if node.type == "root_files":
        return (
            f"files: {node.properties.get('directory', '.')} / "
            f"{node.properties.get('pattern', '*.root')}"
        )
    return node.title


def generate_workflow_summary(project: Project) -> str:
    lines = [
        f"# {project.name}",
        "",
        "Generated workflow order:",
        "",
    ]
    for index, node in enumerate(project.workflow.topological_order(), 1):
        lines.append(f"{index}. **{node.title}** (`{node.type}`)")
        lines.append(f"   - `{command_preview(node)}`")
    lines.extend(
        [
            "",
            "The `.bflow.json` file is the authoritative workflow definition.",
            "Open it in BelleFlow Studio to run locally, submit through LSF, or "
            "export an HTCondor DAG.",
            "",
        ]
    )
    return "\n".join(lines)


def generate_project(project: Project, output_directory: str | Path) -> list[Path]:
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    project_file = output / f"{safe_filename(project.name)}.bflow.json"
    project.save(project_file)
    written.append(project_file)

    for graph in project.loader_graphs.values():
        path = output / f"{safe_filename(graph.name)}.cc"
        path.write_text(generate_loader_cpp(graph), encoding="utf-8")
        written.append(path)

    summary = output / "GENERATED_WORKFLOW.md"
    summary.write_text(generate_workflow_summary(project), encoding="utf-8")
    written.append(summary)
    return written
