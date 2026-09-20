from pathlib import Path
import re

import pytest

from analysis_studio.analysis_modules import MODULE_SPECS
from analysis_studio.codegen import generate_loader_cpp
from analysis_studio.model import Graph, Project
from analysis_studio.registry import NODE_SPECS, specs_for_scope
from analysis_studio.validation import validate_loader_graph, validate_project


def chain(*kinds):
    graph = Graph("program", "Program", "loader")
    nodes = [graph.add_node(NODE_SPECS[k], 0, i * 150) for i, k in enumerate(("loader_decl", *kinds, "loader_end"))]
    for first, second in zip(nodes, nodes[1:]):
        graph.add_edge(first.id, "out", second.id, "in")
    return graph, nodes[1:-1]


@pytest.mark.parametrize("kind", MODULE_SPECS)
def test_every_module_generates_and_survives_save_reload(kind, tmp_path):
    graph, _ = chain(kind)
    code = generate_loader_cpp(graph)
    project = Project.empty()
    project.loader_programs[graph.id] = graph
    path = tmp_path / "project.astudio.json"
    project.save(path)
    restored = Project.load(path)
    assert generate_loader_cpp(restored.loader_programs[graph.id]) == code
    # Every generated Loader call must exist in the vendored public header.
    header = (Path(__file__).parents[1] / "Belle2_analysis/include/Loader.h").read_text()
    for method in re.findall(r"\bloader\.(\w+)\(", code):
        assert re.search(r"\b" + method + r"\(", header), method


def test_palette_categories_and_loader_class_contract():
    assert NODE_SPECS["loader_decl"].label == "Loader Declaration"
    assert "loader_class" not in {p.name for p in NODE_SPECS["loader_decl"].properties}
    assert NODE_SPECS["plot_set"].category == "Plot"
    assert NODE_SPECS["cut_flow"].category == "Selection"
    assert NODE_SPECS["samples"].category == "Input"
    assert NODE_SPECS["fit"].category == "Fit"
    assert not any(s.category == "Analysis tasks" for s in NODE_SPECS.values())
    assert all(NODE_SPECS[k].category == "Advanced" for k in ("cut", "load", "draw_th1d", "define_variable"))
    assert specs_for_scope("loader")[0].category == "Loader"
    assert specs_for_scope("loader")[-1].category == "Advanced"
    graph, _ = chain("samples")
    graph.nodes[0].properties["loader_class"] = "OtherLoader"
    with pytest.raises(ValueError, match="only the Loader class"):
        generate_loader_cpp(graph)


def test_weights_ranking_and_statistics_preserve_argument_order():
    graph, (weight, ranked, variables) = chain("event_weight", "ranked_variables", "variables")
    weight.properties.update(weight_object="MC_weight", header="code/MyWeight.h", mapping=[dict(argument="momentum", expression="first_p")])
    ranked.properties.update(pairs=[dict(rank_expression="p1", value_expression="id1"), dict(rank_expression="p2", value_expression="id2")], outputs=[dict(enabled=True, name="second_ID", rank=1)])
    variables.properties["variables"] = [dict(enabled=True, name="spread", operation="GetDiff", expressions="a;b;c", order=2)]
    code = generate_loader_cpp(graph)
    assert code.index('#include "code/MyWeight.h"') < code.index("int main(")
    assert code.index('EventWeights::Register("MC_weight", MC_weight)') < code.index('.AddWeight("MC_weight", {{"momentum", "first_p"}})')
    assert '.ConditionalPairDefineNewVariable({{"p1", "id1"}, {"p2", "id2"}}, 1, "second_ID")' in code
    assert '.GetDiff({"a", "b", "c"}, 2, "spread")' in code


def test_root_objects_live_until_end_and_export_only_afterwards():
    graph, (histogram, dataset, profile, support) = chain("histogram", "dataset", "profile", "cpp_support")
    histogram.properties.update(callback="map_mass", header="code/map.h", expressions="M\ndeltaE")
    support.properties.update(declarations="double marker = 0;", setup="marker = 1;", after="marker = 2;")
    code = generate_loader_cpp(graph)
    end = code.index("loader.end();")
    assert code.index("TH1D studio_") < code.index(".FillCustomizedTH1D(") < end
    assert code.index(".FillDataSet(") < end
    assert code.index(".FillTProfile(") < end
    assert all(m.start() > end for m in re.finditer(r"TFile output\(", code))
    assert code.index("double marker = 0;") < code.index("int main(")
    assert code.index("marker = 1;") < end < code.index("marker = 2;")
    assert code.count(".Write();") == 3
    assert "RooFit::WeightVar" in code


@pytest.mark.parametrize("metric,fragment", [
    ("Punzi", 'DrawPunziFOM("thrust", 0.0, 1.0, 100, 1000.0, 1.28, 1,'),
    ("FOM", 'DrawFOM("thrust", 0.0, 1.0, 100, 1,'),
    ("AUC", 'CalculateAUC("thrust", 0.0, 1.0, "results/optimization.txt", "w")'),
])
def test_bdt_metric_overload_arguments(metric, fragment):
    graph, (node,) = chain("bdt_evaluate")
    node.properties["metric"] = metric
    assert fragment in generate_loader_cpp(graph)


def test_bdt_training_and_application_keep_variable_order():
    graph, (train, apply) = chain("bdt_train", "bdt_apply")
    train.properties.update(variables="z\nx\ny", balanced=False, name="trained.weightfile")
    apply.properties.update(variables="z\nx\ny", classifier="models/trained.weightfile")
    code = generate_loader_cpp(graph)
    assert 'FastBDTTrain({"z", "x", "y"}, "", "", {{"NTrees", 100.0}' in code
    assert 'false, "models", "trained.weightfile")' in code
    assert 'FastBDTApplication({"z", "x", "y"}, "models/trained.weightfile", "BDT_output")' in code


def test_profile_fit_uses_framework_barrier_and_exports_results():
    graph, _ = chain("profile_fit")
    code = generate_loader_cpp(graph)
    positions = [code.index(name) for name in (".DefineAndFillProfile(", ".DefineTF1(", ".Fit(", ".PlotFit(", ".ExportFitResult(", ".end()")]
    assert positions == sorted(positions)
    assert '"intercept", 1.777, 1.7, 1.85, false' in code


@pytest.mark.parametrize("kind,update,message", [
    ("event_split", dict(parts=2, index=2), "partition"),
    ("ranked_variables", dict(outputs=[dict(name="third", rank=2)]), "rank is outside"),
    ("variables", dict(variables=[dict(name="d", operation="GetDiff", expressions="a;b", order=1)]), "available input pairs"),
    ("variables", dict(variables=[dict(operation="bad")]), "choose one of"),
    ("bdt_train", dict(hyperparameters=[dict(name="Depth", value=1.5)]), "integer"),
    ("bdt_train", dict(hyperparameters=[dict(name="Subsample", value=2)]), "(0, 1]"),
    ("histogram", dict(bins="nan"), "finite int"),
    ("event_weight", dict(weight_object="some_call()"), "C++ symbol"),
    ("event_weight", dict(header="../outside.h"), "project-relative"),
    ("profile_fit", dict(formula="[5]*x"), "parameter index"),
    ("dataset", dict(observables=[dict(name="weight", expression="M", minimum=0, maximum=1)]), "reserved"),
    ("plots_2d", dict(plots=[dict(x_min=2, x_max=1)]), "must be smaller"),
])
def test_invalid_settings_are_reported_before_generation(kind, update, message):
    graph, (node,) = chain(kind)
    node.properties.update(update)
    errors = validate_loader_graph(graph)
    assert any(message in e for e in errors), errors
    with pytest.raises(ValueError):
        generate_loader_cpp(graph)


def test_support_build_inputs_and_freshness(tmp_path, monkeypatch):
    import analysis_studio.build as build
    graph, (support,) = chain("cpp_support")
    support.properties.update(headers="helper.h", sources="helper.cc")
    for filename in ("helper.h", "helper.cc"):
        (tmp_path / filename).write_text("// source\n")
    (tmp_path / "helper.h").write_text('#include "nested.h"\n')
    (tmp_path / "nested.h").write_text("// nested\n")
    project = Project.empty()
    project.loader_programs[graph.id] = graph
    path = tmp_path / "project.astudio.json"
    project.save(path)
    build.generate_code(project, path, log=lambda _: None)
    calls = []
    monkeypatch.setattr(build, "_resolve_framework", lambda *_: tmp_path)
    monkeypatch.setattr(build, "_root_config", lambda *_: [])
    def fake_compile(command, root, log):
        calls.append(command)
        Path(command[command.index("-o") + 1]).write_text("binary")
    monkeypatch.setattr(build, "_run", fake_compile)
    build.compile_project(project, path, log=lambda _: None)
    assert str(tmp_path / "helper.cc") in calls[0]
    assert f"-I{tmp_path}" in calls[0]
    build.ensure_build_current(path)
    (tmp_path / "nested.h").write_text("// changed\n")
    with pytest.raises(RuntimeError, match="Build input file changed"):
        build.ensure_build_current(path)


def test_support_files_missing_on_disk_are_actionable(tmp_path):
    graph, (node,) = chain("event_weight")
    node.properties["header"] = "missing.h"
    project = Project.empty()
    project.loader_programs[graph.id] = graph
    assert any("missing.h" in e and "does not exist" in e for e in validate_project(project, tmp_path))


def test_belle_tau_inventory_is_covered_and_audit_ignores_comments(tmp_path):
    import json
    from analysis_studio.coverage import audit_loader_calls, LOADER_COVERAGE
    inventory = json.loads((Path(__file__).parents[1] / "docs/belle_tau_loader_coverage.json").read_text())
    for row in inventory["methods"]:
        assert LOADER_COVERAGE[row["method"]] in NODE_SPECS
    (tmp_path / "example.cc").write_text('Loader l("tree"); /* l.Bad(); */ l.Cut("x > 0"); // l.Bad2();\n')
    assert [r["method"] for r in audit_loader_calls(tmp_path)["methods"]] == ["Cut"]


def test_same_weight_registers_once_but_is_applied_at_each_position():
    graph, (first, second) = chain("event_weight", "event_weight")
    for node in (first, second):
        node.properties["weight_object"] = "MC_weight"
    code = generate_loader_cpp(graph)
    assert code.count("EventWeights::Register(") == 1
    assert code.count(".AddWeight(") == 2
    second.properties["weight_object"] = "different_object"
    with pytest.raises(ValueError, match="conflicting EventWeight objects"):
        generate_loader_cpp(graph)


@pytest.mark.parametrize("metric,extension", [("Punzi", "png"), ("FOM", "png"), ("AUC", "txt")])
def test_general_variable_optimization_output_format(metric, extension):
    graph, (node,) = chain("bdt_evaluate")
    node.properties.update(metric=metric, expression="muon_p", output_directory="results/scans",
                           output_name="momentum.v2.png")
    code = generate_loader_cpp(graph)
    assert '"muon_p"' in code
    assert f'"results/scans/momentum.v2.{extension}"' in code
    assert "FastBDT" not in code


@pytest.mark.parametrize("title", ["BDT Performance", "My momentum scan"])
def test_legacy_optimizer_migrates_path_and_preserves_analysis(tmp_path, title):
    graph, (node,) = chain("bdt_evaluate")
    node.title = title
    node.properties.pop("output_directory")
    node.properties.pop("output_name")
    node.properties.update(filename=r"plots\momentum.v2.png", expression="muon_p", metric="AUC")
    project = Project.empty()
    project.loader_programs[graph.id] = graph
    path = tmp_path / "legacy.astudio.json"
    project.save(path)
    restored = Project.load(path)
    migrated = next(n for n in restored.loader_programs[graph.id].nodes if n.id == node.id)
    assert migrated.title == ("Variable Optimization" if title == "BDT Performance" else title)
    assert migrated.properties["expression"] == "muon_p"
    assert migrated.properties["output_directory"] == "plots"
    assert migrated.properties["output_name"] == "momentum.v2"
    assert "filename" not in migrated.properties
    assert '"plots/momentum.v2.txt"' in generate_loader_cpp(restored.loader_programs[graph.id])


@pytest.mark.parametrize("name", ["folder/scan", "../scan", ".PNG", ".txt"])
def test_optimizer_rejects_invalid_output_names(name):
    graph, (node,) = chain("bdt_evaluate")
    node.properties["output_name"] = name
    assert any("output_name" in error for error in validate_loader_graph(graph))
