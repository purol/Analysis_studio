from copy import deepcopy

import pytest

from analysis_studio.codegen import generate_loader_cpp
from analysis_studio.model import Graph, Project
from analysis_studio.recipes import add_analysis_starter, parameter_preset
from analysis_studio.registry import NODE_SPECS
from analysis_studio.validation import validate_loader_graph


def chain(*kinds):
    graph = Graph("analysis", "Analysis", "loader")
    nodes = [graph.add_node(NODE_SPECS[k], i * 250, 0) for i, k in enumerate(("loader_decl", *kinds, "loader_end"))]
    for first, second in zip(nodes, nodes[1:]):
        graph.add_edge(first.id, "out", second.id, "in")
    return graph, nodes[1:-1]


def test_cut_flow_order_disabled_stages_and_diagnostic_filenames():
    graph, (flow,) = chain("cut_flow")
    flow.properties["plot_timing"] = "both"
    flow.properties["steps"] = [
        dict(name="first", condition="M > 1.7", report=True),
        dict(name="disabled", condition="BAD", enabled=False),
        dict(name="last", condition="deltaE < 0.15", report=False),
    ]
    code = generate_loader_cpp(graph)
    assert code.index("_01_before_M.png") < code.index('Cut("M > 1.7")') < code.index('PrintInformation("first")') < code.index("_01_after_M.png")
    assert "BAD" not in code and 'PrintInformation("last")' not in code
    assert code.count(".DrawTH1D(") == 4
    assert "_03_after_M.png" in code
    assert code.index('Cut("deltaE < 0.15")') < code.index(".end()")


def test_fit_uses_official_queued_loader_api_with_unique_object_ids():
    graph, (first, second) = chain("fit", "fit")
    first.properties.update(use_fit_range=True, result_filename="results/parameters.root", workspace_filename="results/workspace.root")
    second.properties.update(model="RooBifurGauss", parameters=parameter_preset("RooBifurGauss"), plot_filename="second.png")
    code = generate_loader_cpp(graph)
    operations = [".DefineObservable(", ".SetRange(", ".DefineAndFillDataSet(", ".DefineFitParameter(", ".DefineModel(", ".Fit(", ".PlotFit(", ".ExportFitResult(", ".SaveWorkspace("]
    positions = [code.index(op) for op in operations]
    assert positions == sorted(positions)
    assert code.rindex(".Fit(") < code.index(".end()")
    assert "RooBifurGauss" in code and "fit_options.range =" in code
    assert "fitmanager" not in code  # private implementation must not be accessed
    datasets = [line for line in code.splitlines() if ".DefineAndFillDataSet(" in line]
    assert len(datasets) == len(set(datasets)) == 2


@pytest.mark.parametrize("kind,update,expected", [
    ("fit", {"minimum": 2, "maximum": 1}, "minimum must be smaller"),
    ("fit", {"model": "RooBifurGauss"}, "needs 3 parameters"),
    ("fit", {"bins": "nan"}, "finite positive integer"),
    ("fit", {"use_fit_range": True, "fit_maximum": 99}, "inside the observable"),
    ("fit", {"workspace_filename": "fit.png"}, "different filenames"),
    ("cut_flow", {"steps": [{"condition": ""}]}, "cut expression is empty"),
    ("cut_flow", {"steps": "wrong"}, "add at least one row"),
    ("plot_set", {"plots": [{"bins": 0}]}, "finite positive integer"),
    ("plot_set", {"plots": [{"filename": "../bad.png"}]}, "without directories"),
    ("samples", {"samples": [{"argument": -1}]}, "argv number"),
])
def test_invalid_recipe_is_reported_before_generation(kind, update, expected):
    graph, (node,) = chain(kind)
    node.properties.update(update)
    assert any(expected in error for error in validate_loader_graph(graph))
    with pytest.raises(ValueError, match=expected):
        generate_loader_cpp(graph)


def test_samples_literal_paths_are_escaped_and_runtime_arguments_checked():
    graph, (samples,) = chain("samples")
    samples.properties["samples"] = [
        dict(directory='input/"data"', including=".root", label="DATA"),
        dict(argument=2, including="signal", label="SIGNAL", condition="M > 1.5"),
    ]
    code = generate_loader_cpp(graph)
    assert 'Load("input/\\"data\\"", ".root", "DATA")' in code
    assert "if (argc <= 2)" in code
    assert 'LoadWithCut(argv[2], "signal", "SIGNAL", "M > 1.5")' in code


def test_starter_and_recipe_round_trip_and_independent_defaults(tmp_path):
    project = Project.empty("Example")
    graph = project.create_loader_program("Selections")
    add_analysis_starter(graph)
    assert not validate_loader_graph(graph)
    before = generate_loader_cpp(graph)
    path = tmp_path / "analysis.astudio.json"
    project.save(path)
    restored = Project.load(path)
    assert generate_loader_cpp(restored.loader_programs[graph.id]) == before
    first = graph.add_node(NODE_SPECS["fit"], 0, 0)
    second = graph.add_node(NODE_SPECS["fit"], 0, 0)
    defaults = deepcopy(NODE_SPECS["fit"].defaults())
    first.properties["parameters"][0]["value"] = 42
    assert second.properties == defaults == NODE_SPECS["fit"].defaults()


def test_loader_names_are_assigned_without_manual_cpp_edits():
    graph = Graph("analysis", "Analysis", "loader")
    first = graph.add_node(NODE_SPECS["loader_decl"], 0, 0)
    second = graph.add_node(NODE_SPECS["loader_decl"], 0, 0)
    assert first.properties["variable_name"] != second.properties["variable_name"]
