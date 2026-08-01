from pathlib import Path

from analysis_studio.model import Graph, Project
from analysis_studio.planning import build_execution_plan
from analysis_studio.registry import FOREACH_DEFAULTS, NODE_SPECS


def add(graph, node_type, title=None, x=0, y=0, **properties):
    node = graph.add_node(NODE_SPECS[node_type], x, y, title)
    node.properties.update(properties)
    return node


def connect(graph, first, second):
    graph.add_edge(first.id, "out", second.id, "in")



def ensure_echo_source(root: Path) -> None:
    code = root / "code"
    code.mkdir(exist_ok=True)
    (code / "echo.py").write_text("print('ok')\n", encoding="utf-8")


def valid_loader_program():
    graph = Graph("analysis_loader", "Analysis loader", "loader")
    declaration = add(graph, "loader_decl", variable_name="loader", branch="my_tree")
    ending = add(graph, "loader_end")
    connect(graph, declaration, ending)
    return graph


def test_visible_for_each_region_and_wait_expand_to_dependencies(tmp_path: Path):
    ensure_echo_source(tmp_path)
    for name in ["a.root", "b.root"]:
        (tmp_path / name).write_text("", encoding="utf-8")

    project = Project(
        name="test",
        workflow=Graph("workflow", "Workflow", "workflow"),
        loader_programs={"analysis_loader": valid_loader_program()},
    )
    prepare = add(
        project.workflow,
        "custom_command",
        "Prepare",
        code="code/echo.py",
        output_name="echo",
        argv="prepare",
    )
    execute = add(
        project.workflow,
        "loader_execute",
        "Analyze file",
        x=100,
        y=100,
        loader_program="analysis_loader",
        code="code/echo.py",
        output_name="echo",
        argv="{file_name}\n{file_stem}\n{loop_index}",
        job_name="Analyze_{loop_index}",
    )
    properties = dict(FOREACH_DEFAULTS)
    properties.update(
        {
            "source_mode": "root_files",
            "directory": str(tmp_path),
            "pattern": "*.root",
            "tokens": [
                {"name": "file_name", "source": "filename"},
                {"name": "file_stem", "source": "stem"},
                {"name": "loop_index", "source": "index"},
            ],
        }
    )
    region = project.workflow.add_region(50, 50, properties, title="Analyze all ROOT files")
    region.member_node_ids = [execute.id]

    barrier = add(
        project.workflow,
        "wait",
        "All ready",
        wait_for="Prepare\nAnalyze file",
    )
    finish = add(
        project.workflow,
        "custom_command",
        "Fit",
        code="code/echo.py",
        output_name="echo",
        argv="fit",
    )
    connect(project.workflow, barrier, finish)

    plan = build_execution_plan(project, tmp_path)
    assert len(plan.tasks) == 4
    analysis = [task for task in plan.tasks if "Analyze file" in task.title]
    assert {task.executable for task in analysis} == {str((tmp_path / "bin" / "Analysis_loader").resolve())}
    assert [task.argv[0] for task in analysis] == ["a.root", "b.root"]
    fit = next(task for task in plan.tasks if task.title == "Fit")
    assert len(fit.dependencies) == 3
    assert {task.id for task in analysis}.issubset(set(fit.dependencies))
    assert next(task for task in plan.tasks if task.title == "Prepare").id in fit.dependencies


def test_csv_columns_are_iteration_variables(tmp_path: Path):
    ensure_echo_source(tmp_path)
    csv_path = tmp_path / "points.csv"
    csv_path.write_text("mass,sample name\n1.0,A\n2.0,B\n", encoding="utf-8")
    project = Project(
        name="csv",
        workflow=Graph("workflow", "Workflow", "workflow"),
        loader_programs={},
    )
    command = add(
        project.workflow,
        "custom_command",
        "Row command",
        code="code/echo.py",
        output_name="echo",
        argv="{mass_value}\n{sample}\n{row_number}",
    )
    properties = dict(FOREACH_DEFAULTS)
    properties.update(
        {
            "source_mode": "csv_rows",
            "csv_file": str(csv_path),
            "tokens": [
                {"name": "mass_value", "source": "column:mass"},
                {"name": "sample", "source": "column:sample name"},
                {"name": "row_number", "source": "index"},
            ],
        }
    )
    region = project.workflow.add_region(0, 0, properties, title="Rows")
    region.member_node_ids = [command.id]

    plan = build_execution_plan(project, tmp_path)
    assert [task.argv for task in plan.tasks] == [
        ("1.0", "A", "0"),
        ("2.0", "B", "1"),
    ]


def test_nested_for_each_combines_outer_and_inner_variables(tmp_path: Path):
    ensure_echo_source(tmp_path)
    project = Project(
        name="nested",
        workflow=Graph("workflow", "Workflow", "workflow"),
        loader_programs={},
    )
    command = add(
        project.workflow,
        "custom_command",
        "Combination",
        code="code/echo.py",
        output_name="echo",
        argv="{sample}\n{mass}",
    )

    outer_properties = dict(FOREACH_DEFAULTS)
    outer_properties.update(
        {
            "source_mode": "values",
            "values": "signal\nbackground",
            "tokens": [{"name": "sample", "source": "value"}],
        }
    )
    outer = project.workflow.add_region(
        0, 0, outer_properties, title="Samples", width=700, height=500
    )

    inner_properties = dict(FOREACH_DEFAULTS)
    inner_properties.update(
        {
            "source_mode": "values",
            "values": "1.0\n2.0",
            "tokens": [{"name": "mass", "source": "value"}],
        }
    )
    inner = project.workflow.add_region(
        100, 100, inner_properties, title="Masses", width=400, height=250
    )
    inner.parent_region_id = outer.id
    inner.member_node_ids = [command.id]

    plan = build_execution_plan(project, tmp_path)
    assert [task.argv for task in plan.tasks] == [
        ("signal", "1.0"),
        ("signal", "2.0"),
        ("background", "1.0"),
        ("background", "2.0"),
    ]
    assert all(len(task.concurrency_limits) == 0 for task in plan.tasks)


def test_nested_source_can_use_outer_variable(tmp_path: Path):
    ensure_echo_source(tmp_path)
    project = Project(
        name="nested-source",
        workflow=Graph("workflow", "Workflow", "workflow"),
        loader_programs={},
    )
    command = add(
        project.workflow,
        "custom_command",
        "Combination",
        code="code/echo.py",
        output_name="echo",
        argv="{sample}\n{variant}",
    )
    outer_properties = dict(FOREACH_DEFAULTS)
    outer_properties.update(
        {
            "source_mode": "values",
            "values": "A\nB",
            "tokens": [{"name": "sample", "source": "value"}],
        }
    )
    outer = project.workflow.add_region(
        0, 0, outer_properties, title="Samples", width=700, height=500
    )
    inner_properties = dict(FOREACH_DEFAULTS)
    inner_properties.update(
        {
            "source_mode": "values",
            "values": "{sample}_up\n{sample}_down",
            "tokens": [{"name": "variant", "source": "value"}],
        }
    )
    inner = project.workflow.add_region(
        100, 100, inner_properties, title="Variants", width=400, height=250
    )
    inner.parent_region_id = outer.id
    inner.member_node_ids = [command.id]

    plan = build_execution_plan(project, tmp_path)
    assert [task.argv for task in plan.tasks] == [
        ("A", "A_up"),
        ("A", "A_down"),
        ("B", "B_up"),
        ("B", "B_down"),
    ]


def test_nested_concurrency_limits_are_inherited(tmp_path: Path):
    ensure_echo_source(tmp_path)
    project = Project(
        name="nested-limits",
        workflow=Graph("workflow", "Workflow", "workflow"),
        loader_programs={},
    )
    command = add(
        project.workflow,
        "custom_command",
        "Combination",
        code="code/echo.py",
        output_name="echo",
        argv="{outer_value}\n{inner_value}",
    )
    outer_properties = dict(FOREACH_DEFAULTS)
    outer_properties.update(
        {
            "source_mode": "values",
            "values": "A\nB",
            "max_parallel": 2,
            "tokens": [{"name": "outer_value", "source": "value"}],
        }
    )
    outer = project.workflow.add_region(0, 0, outer_properties, width=700, height=500)
    inner_properties = dict(FOREACH_DEFAULTS)
    inner_properties.update(
        {
            "source_mode": "values",
            "values": "1\n2",
            "max_parallel": 3,
            "tokens": [{"name": "inner_value", "source": "value"}],
        }
    )
    inner = project.workflow.add_region(100, 100, inner_properties, width=400, height=250)
    inner.parent_region_id = outer.id
    inner.member_node_ids = [command.id]
    plan = build_execution_plan(project, tmp_path)
    assert all(len(task.concurrency_limits) == 2 for task in plan.tasks)
    assert all(sorted(task.concurrency_limits.values()) == [2, 3] for task in plan.tasks)
