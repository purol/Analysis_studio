from analysis_studio.model import Graph, Project
from analysis_studio.registry import NODE_SPECS
from analysis_studio.validation import validate_loader_graph


def add(graph, node_type, title=None, **properties):
    node = graph.add_node(NODE_SPECS[node_type], 0, 0, title)
    node.properties.update(properties)
    return node


def connect(graph, first, second):
    graph.add_edge(first.id, "out", second.id, "in")


def test_loader_declaration_and_end_are_chain_boundaries():
    assert NODE_SPECS["loader_decl"].inputs == ()
    assert NODE_SPECS["loader_end"].outputs == ()

    graph = Graph("g", "Program", "loader")
    first = add(graph, "loader_decl", "First", variable_name="first", branch="tree_a")
    first_end = add(graph, "loader_end", "End first")
    second = add(graph, "loader_decl", "Second", variable_name="second", branch="tree_b")
    second_end = add(graph, "loader_end", "End second")
    connect(graph, first, first_end)
    connect(graph, second, second_end)

    assert validate_loader_graph(graph) == []
    assert [node.id for node in graph.ordered_roots()] == [first.id, second.id]
    graph.set_root_order(second.id, 1)
    assert [node.id for node in graph.ordered_roots()] == [second.id, first.id]


def test_graph_rejects_cycle():
    graph = Graph("g", "Workflow", "workflow")
    a = add(graph, "custom_command", "A")
    b = add(graph, "custom_command", "B")
    connect(graph, a, b)
    try:
        connect(graph, b, a)
    except ValueError as exc:
        assert "Cycles" in str(exc)
    else:
        raise AssertionError("cycle was accepted")


def test_v2_hidden_for_each_migrates_to_visible_region():
    data = {
        "name": "legacy-v2",
        "version": 2,
        "backend": "local",
        "backend_options": {},
        "workflow": {
            "id": "workflow",
            "name": "Workflow",
            "scope": "workflow",
            "nodes": [
                {
                    "id": "loop",
                    "type": "for_each",
                    "title": "ROOT files",
                    "x": 10,
                    "y": 20,
                    "properties": {
                        "body_graph": "body",
                        "source_mode": "root_files",
                        "directory": "./Ntuple",
                        "pattern": "*.root",
                        "csv_file": "",
                        "delimiter": ",",
                        "has_header": True,
                        "values": "",
                        "variable_name": "item",
                        "max_parallel": 0,
                    },
                }
            ],
            "edges": [],
        },
        "loader_programs": {},
        "foreach_graphs": {
            "body": {
                "id": "body",
                "name": "Body",
                "scope": "workflow",
                "nodes": [
                    {
                        "id": "execute",
                        "type": "custom_command",
                        "title": "Analyze",
                        "x": 0,
                        "y": 0,
                        "properties": NODE_SPECS["custom_command"].defaults(),
                    }
                ],
                "edges": [],
            }
        },
    }
    project = Project.from_dict(data)
    assert project.version == 10
    assert [node.title for node in project.workflow.nodes] == ["Analyze"]
    assert len(project.workflow.foreach_regions) == 1
    assert project.workflow.foreach_regions[0].member_node_ids == ["execute"]
    assert project.workflow.foreach_regions[0].properties["tokens"] == []
    assert project.foreach_graphs == {}


def test_v1_per_file_workflow_migrates_through_visible_region():
    data = {
        "name": "legacy-v1",
        "version": 1,
        "backend": "local",
        "backend_options": {},
        "workflow": {
            "id": "workflow",
            "name": "Workflow",
            "scope": "workflow",
            "nodes": [
                {
                    "id": "files",
                    "type": "root_files",
                    "title": "Files",
                    "x": 0,
                    "y": 0,
                    "properties": {"directory": "./Ntuple", "pattern": "*.root"},
                },
                {
                    "id": "analysis",
                    "type": "analysis_stage",
                    "title": "Analyze",
                    "x": 200,
                    "y": 0,
                    "properties": {
                        "executable": "./Analysis_main",
                        "arguments": "{input_dir} {filename}",
                        "output_dir": "./output",
                        "run_mode": "per_file",
                        "pipeline": "main",
                        "job_name": "Analyze",
                    },
                },
            ],
            "edges": [
                {
                    "id": "edge",
                    "source": "files",
                    "source_port": "out",
                    "target": "analysis",
                    "target_port": "in",
                }
            ],
        },
        "loader_graphs": {
            "main": {
                "id": "main",
                "name": "Main",
                "scope": "loader",
                "nodes": [],
                "edges": [],
            }
        },
    }
    project = Project.from_dict(data)
    assert project.version == 10
    assert project.workflow.nodes[0].type == "loader_execute"
    assert project.workflow.nodes[0].properties["argv"] == "{directory}\n{filename}"
    assert project.workflow.foreach_regions[0].properties["tokens"] == [
        {"name": "directory", "source": "directory"},
        {"name": "filename", "source": "filename"},
    ]
    assert project.workflow.foreach_regions[0].member_node_ids == [
        project.workflow.nodes[0].id
    ]


def test_v4_region_geometry_migrates_to_nested_parent():
    data = {
        "name": "legacy-v4-nested-layout",
        "version": 4,
        "backend": "local",
        "backend_options": {},
        "workflow": {
            "id": "workflow",
            "name": "Workflow",
            "scope": "workflow",
            "nodes": [],
            "edges": [],
            "root_order": [],
            "foreach_regions": [
                {
                    "id": "outer",
                    "title": "Outer",
                    "x": 0,
                    "y": 0,
                    "width": 700,
                    "height": 500,
                    "properties": {},
                    "member_node_ids": [],
                },
                {
                    "id": "inner",
                    "title": "Inner",
                    "x": 100,
                    "y": 100,
                    "width": 300,
                    "height": 200,
                    "properties": {},
                    "member_node_ids": [],
                },
            ],
        },
        "loader_programs": {},
        "foreach_graphs": {},
    }
    project = Project.from_dict(data)
    assert project.version == 10
    assert project.workflow.region("outer").parent_region_id is None
    assert project.workflow.region("inner").parent_region_id == "outer"


def test_empty_project_starts_without_loader_programs():
    project = Project.empty()
    assert project.loader_programs == {}
    assert project.workflow.scope == "workflow"


def test_v5_loader_execute_executable_is_removed_during_migration():
    data = {
        "name": "legacy-v5",
        "version": 5,
        "workflow": {
            "id": "workflow",
            "name": "Workflow",
            "scope": "workflow",
            "nodes": [
                {
                    "id": "run",
                    "type": "loader_execute",
                    "title": "Run",
                    "properties": {
                        "loader_program": "loader",
                        "executable": "/old/manual/path",
                        "argv": "",
                        "working_directory": "",
                        "output_dir": "",
                        "job_name": "Run",
                    },
                }
            ],
            "edges": [],
        },
        "loader_programs": {
            "loader": {
                "id": "loader",
                "name": "My Loader",
                "scope": "loader",
                "nodes": [],
                "edges": [],
            }
        },
    }
    project = Project.from_dict(data)
    assert project.version == 10
    assert "executable" not in project.workflow.node("run").properties


def test_loader_program_can_be_renamed_and_deleted_with_references_cleared():
    project = Project.empty()
    graph = Graph("loader_a", "Loader A", "loader")
    project.loader_programs[graph.id] = graph
    execute = project.workflow.add_node(NODE_SPECS["loader_execute"], 0, 0)
    execute.properties["loader_program"] = graph.id

    project.rename_loader_program(graph.id, "Renamed Loader")
    assert graph.name == "Renamed Loader"

    cleared = project.remove_loader_program(graph.id)
    assert cleared == [execute.id]
    assert project.loader_programs == {}
    assert execute.properties["loader_program"] == ""


def test_loader_program_generated_names_must_be_unique():
    project = Project.empty()
    first = project.create_loader_program("Analysis Main")
    assert first.name == "Analysis Main"
    try:
        project.create_loader_program("Analysis_Main")
    except ValueError as exc:
        assert "generated executable name" in str(exc)
    else:
        raise AssertionError("Expected generated-name collision to be rejected")


def test_v7_backend_and_execution_properties_migrate_to_v8():
    data = {
        "name": "legacy-v7",
        "version": 7,
        "backend": "lsf",
        "backend_options": {
            "local_workers": 8,
            "lsf_queue": "l",
            "lsf_poll_seconds": 5,
            "lsf_max_inflight": 123,
            "lsf_cancel_on_failure": False,
            "condor_universe": "vanilla",
        },
        "workflow": {
            "id": "workflow",
            "name": "Workflow",
            "scope": "workflow",
            "nodes": [
                {
                    "id": "cmd",
                    "type": "custom_command",
                    "title": "Command",
                    "properties": {
                        "code": "code/run.py",
                        "output_name": "manual_name",
                        "build_mode": "copy",
                        "use_analysis_framework": False,
                        "local_max_parallel": 4,
                        "lsf_queue": "",
                        "lsf_max_inflight": 7,
                        "lsf_extra_options": "-R\nrusage[mem=1000]",
                    },
                }
            ],
            "edges": [],
            "foreach_regions": [
                {
                    "id": "loop",
                    "title": "Loop",
                    "x": 0,
                    "y": 0,
                    "width": 400,
                    "height": 200,
                    "properties": {"source_mode": "values", "values": "a", "max_parallel": 9, "tokens": []},
                    "member_node_ids": ["cmd"],
                    "parent_region_id": None,
                }
            ],
        },
        "loader_programs": {},
    }
    project = Project.from_dict(data)
    assert project.version == 10
    assert project.backend_options["lsf_max_active_jobs"] == 123
    assert "lsf_queue" not in project.backend_options
    assert "lsf_max_inflight" not in project.backend_options
    node = project.workflow.node("cmd")
    assert node.properties["lsf_queue"] == "l"
    assert node.properties["build_mode"] == "auto"
    for key in (
        "output_name",
        "use_analysis_framework",
        "local_max_parallel",
        "lsf_max_inflight",
        "lsf_extra_options",
    ):
        assert key not in node.properties
    assert "max_parallel" not in project.workflow.region("loop").properties


def test_v8_backend_settings_have_only_global_fields():
    project = Project.empty()
    assert set(project.backend_options) == {
        "local_workers",
        "lsf_poll_seconds",
        "lsf_max_active_jobs",
        "lsf_cancel_on_failure",
        "condor_universe",
    }


def test_v8_execution_directory_properties_migrate_to_v9_mkdir_p():
    data = {
        "name": "legacy-v8",
        "version": 8,
        "workflow": {
            "id": "workflow",
            "name": "Workflow",
            "scope": "workflow",
            "nodes": [
                {
                    "id": "cmd",
                    "type": "custom_command",
                    "title": "Command",
                    "properties": {
                        "code": "code/run.py",
                        "argv": "",
                        "working_directory": "old/work",
                        "output_dir": "results",
                        "job_name": "Legacy job",
                        "lsf_queue": "s",
                    },
                }
            ],
            "edges": [],
            "foreach_regions": [],
        },
        "loader_programs": {},
    }
    project = Project.from_dict(data)
    props = project.workflow.node("cmd").properties
    assert project.version == 10
    assert props["mkdir_p"] == "results"
    assert "working_directory" not in props
    assert "output_dir" not in props
    assert "job_name" not in props
    assert props["log_err_prefix"] == ""
    assert props["log_err_suffix"] == ""


def test_v9_log_affixes_and_multiline_mkdir_migrate_to_v10():
    data = {
        "name": "legacy-v9",
        "version": 9,
        "workflow": {
            "id": "workflow",
            "name": "Workflow",
            "scope": "workflow",
            "nodes": [
                {
                    "id": "cmd",
                    "type": "custom_command",
                    "title": "Command",
                    "properties": {
                        "code": "code/run.py",
                        "argv": "",
                        "mkdir_p": "results\nplots with space",
                        "log_prefix": "common_",
                        "log_suffix": "_done",
                        "err_prefix": "error_",
                        "err_suffix": "_failed",
                        "lsf_queue": "s",
                    },
                }
            ],
            "edges": [],
            "foreach_regions": [],
        },
        "loader_programs": {},
    }
    project = Project.from_dict(data)
    props = project.workflow.node("cmd").properties
    assert project.version == 10
    assert props["mkdir_p"] == "results 'plots with space'"
    assert props["log_err_prefix"] == "common_"
    assert props["log_err_suffix"] == "_done"
    for removed in ("log_prefix", "log_suffix", "err_prefix", "err_suffix"):
        assert removed not in props
