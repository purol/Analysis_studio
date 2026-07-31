from pathlib import Path

from analysis_studio.foreach_tokens import source_fields, token_definitions
from analysis_studio.model import Graph, Project
from analysis_studio.registry import FOREACH_DEFAULTS, NODE_SPECS, foreach_properties_for_mode
from analysis_studio.validation import validate_project


def add(graph, node_type, title=None, **properties):
    node = graph.add_node(NODE_SPECS[node_type], 0, 0, title)
    node.properties.update(properties)
    return node


def test_source_mode_exposes_only_relevant_properties():
    assert [p.name for p in foreach_properties_for_mode("root_files")] == [
        "source_mode",
        "directory",
        "pattern",
        "max_parallel",
    ]
    assert [p.name for p in foreach_properties_for_mode("csv_rows")] == [
        "source_mode",
        "csv_file",
        "delimiter",
        "has_header",
        "max_parallel",
    ]
    assert [p.name for p in foreach_properties_for_mode("values")] == [
        "source_mode",
        "values",
        "max_parallel",
    ]


def test_csv_source_fields_and_previews_come_from_real_header(tmp_path: Path):
    path = tmp_path / "points.csv"
    path.write_text("mass,sample name\n1.25,signal\n", encoding="utf-8")
    properties = dict(FOREACH_DEFAULTS)
    properties.update(
        {
            "source_mode": "csv_rows",
            "csv_file": str(path),
            "tokens": [
                {"name": "mass_value", "source": "column:mass"},
                {"name": "sample", "source": "column:sample name"},
            ],
        }
    )
    fields = {field.key: field for field in source_fields(properties, tmp_path)}
    assert fields["column:mass"].preview == "1.25"
    assert fields["column:sample name"].preview == "signal"
    definitions = token_definitions(properties, tmp_path)
    assert [(item.expression, item.preview) for item in definitions] == [
        ("{mass_value}", "1.25"),
        ("{sample}", "signal"),
    ]


def test_undefined_and_outside_tokens_are_validation_errors():
    workflow = Graph("workflow", "Workflow", "workflow")
    inside = add(
        workflow,
        "custom_command",
        "Inside",
        executable="/bin/echo",
        argv="{not_defined}",
    )
    outside = add(
        workflow,
        "custom_command",
        "Outside",
        executable="/bin/echo",
        argv="{also_not_allowed}",
    )
    properties = dict(FOREACH_DEFAULTS)
    properties.update(
        {
            "source_mode": "values",
            "values": "A\nB",
            "tokens": [{"name": "chosen_value", "source": "value"}],
        }
    )
    region = workflow.add_region(0, 0, properties)
    region.member_node_ids = [inside.id]
    project = Project("tokens", workflow, {})
    errors = validate_project(project)
    assert any("{not_defined}" in error and "undefined" in error for error in errors)
    assert any("{also_not_allowed}" in error and "only inside" in error for error in errors)


def test_nested_regions_inherit_outer_variables_and_reject_shadowing():
    workflow = Graph("workflow", "Workflow", "workflow")
    command = add(
        workflow,
        "custom_command",
        "Nested command",
        executable="/bin/echo",
        argv="{sample}\n{mass}",
    )
    outer_properties = dict(FOREACH_DEFAULTS)
    outer_properties.update(
        {
            "source_mode": "values",
            "values": "A\nB",
            "tokens": [{"name": "sample", "source": "value"}],
        }
    )
    outer = workflow.add_region(0, 0, outer_properties, width=700, height=500)
    inner_properties = dict(FOREACH_DEFAULTS)
    inner_properties.update(
        {
            "source_mode": "values",
            "values": "1\n2",
            "tokens": [{"name": "mass", "source": "value"}],
        }
    )
    inner = workflow.add_region(100, 100, inner_properties, width=400, height=250)
    inner.parent_region_id = outer.id
    inner.member_node_ids = [command.id]
    project = Project("nested", workflow, {})
    assert validate_project(project) == []

    inner.properties["tokens"] = [{"name": "sample", "source": "value"}]
    errors = validate_project(project)
    assert any("conflicts" in error and "sample" in error for error in errors)


def test_nested_source_rejects_unknown_non_outer_variable():
    workflow = Graph("workflow", "Workflow", "workflow")
    command = add(workflow, "custom_command", executable="/bin/echo", argv="{inner}")
    outer_properties = dict(FOREACH_DEFAULTS)
    outer_properties.update(
        {
            "source_mode": "values",
            "values": "A",
            "tokens": [{"name": "outer", "source": "value"}],
        }
    )
    outer = workflow.add_region(0, 0, outer_properties, width=700, height=500)
    inner_properties = dict(FOREACH_DEFAULTS)
    inner_properties.update(
        {
            "source_mode": "values",
            "values": "{not_outer}",
            "tokens": [{"name": "inner", "source": "value"}],
        }
    )
    inner = workflow.add_region(100, 100, inner_properties, width=400, height=250)
    inner.parent_region_id = outer.id
    inner.member_node_ids = [command.id]
    errors = validate_project(Project("nested", workflow, {}))
    assert any("not_outer" in error and "outer For Each" in error for error in errors)
