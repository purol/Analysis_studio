from pathlib import Path

from analysis_studio.execution import HTCondorExporter, LocalExecutor
from analysis_studio.model import Graph, Project
from analysis_studio.registry import NODE_SPECS


def add(graph, node_type, title=None, **properties):
    node = graph.add_node(NODE_SPECS[node_type], 0, 0, title)
    node.properties.update(properties)
    return node


def connect(graph, first, second):
    graph.add_edge(first.id, "out", second.id, "in")


def test_local_executor_runs_dependency_graph(tmp_path: Path):
    project = Project(
        name="local",
        workflow=Graph("workflow", "Workflow", "workflow"),
        loader_programs={},
        backend_options={"local_workers": 2, "lsf_queue": "s", "condor_universe": "vanilla"},
    )
    output = tmp_path / "result.txt"
    first = add(
        project.workflow,
        "custom_command",
        "First",
        executable="/bin/sh",
        argv=f"-c\nprintf first > {output}",
    )
    second = add(
        project.workflow,
        "custom_command",
        "Second",
        executable="/bin/sh",
        argv=f"-c\nprintf second >> {output}",
    )
    connect(project.workflow, first, second)
    LocalExecutor(lambda _: None).run(project, tmp_path)
    assert output.read_text(encoding="utf-8") == "firstsecond"


def test_condor_export_preserves_wait_dependencies(tmp_path: Path):
    project = Project(
        name="condor",
        workflow=Graph("workflow", "Workflow", "workflow"),
        loader_programs={},
    )
    add(project.workflow, "custom_command", "A", executable="/bin/echo", argv="A")
    add(project.workflow, "custom_command", "B", executable="/bin/echo", argv="B")
    wait = add(project.workflow, "wait", "Wait AB", wait_for="A\nB")
    c = add(project.workflow, "custom_command", "C", executable="/bin/echo", argv="C")
    connect(project.workflow, wait, c)

    dag = HTCondorExporter(lambda _: None).export(project, tmp_path / "dag", tmp_path)
    text = dag.read_text(encoding="utf-8")
    assert text.count("JOB ") == 3
    parent_lines = [line for line in text.splitlines() if line.startswith("PARENT")]
    assert len(parent_lines) == 1
    assert parent_lines[0].count("task_") == 3
