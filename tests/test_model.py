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
    assert project.version == 5
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
    assert project.version == 5
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
    assert project.version == 5
    assert project.workflow.region("outer").parent_region_id is None
    assert project.workflow.region("inner").parent_region_id == "outer"
