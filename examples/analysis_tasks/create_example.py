"""Regenerate the small, editable tau-analysis GUI example."""
from pathlib import Path

from analysis_studio.codegen import generate_loader_cpp
from analysis_studio.model import Project
from analysis_studio.recipes import parameter_preset
from analysis_studio.registry import NODE_SPECS


def make_project():
    project = Project.empty("Tau selection and fits")
    project.build_options["belle2_analysis_dir"] = "../../Belle2_analysis"
    graph = project.create_loader_program("tau_selection")
    previous = None
    nodes = {}
    for i, (key, kind, title) in enumerate([
        ("source", "loader_decl", "Tau tree"),
        ("samples", "samples", "Signal input"),
        ("cuts", "cut_flow", "Preselection"),
        ("plots", "plot_set", "Selected distributions"),
        ("mass", "fit", "Mass fit"),
        ("energy", "fit", "Energy fit"),
        ("end", "loader_end", "Run analysis"),
    ]):
        node = graph.add_node(NODE_SPECS[kind], i * 290, 70, title)
        nodes[key] = node
        if previous:
            graph.add_edge(previous.id, "out", node.id, "in")
        previous = node
    nodes["samples"].properties["samples"][0]["argument"] = 1
    nodes["cuts"].properties["steps"] = [
        dict(enabled=True, name="Energy window", condition="(-0.3 < deltaE) && (deltaE < 0.15)", report=True),
        dict(enabled=True, name="Mass window", condition="(1.71 < M) && (M < 1.82)", report=True),
        dict(enabled=False, name="Leading muon ID", condition="0.5 < first_muon_muonID", report=True),
        dict(enabled=False, name="Second muon ID", condition="0.5 < second_muon_muonID", report=True),
    ]
    nodes["plots"].properties["plots"].append(dict(enabled=True, expression="deltaE", title=";deltaE [GeV];Events", bins=60, minimum=-0.3, maximum=0.15, filename="deltaE.png"))
    nodes["mass"].properties.update(
        model="RooBifurGauss", parameters=parameter_preset("RooBifurGauss"),
        minimum=1.71, maximum=1.82, use_fit_range=True,
        plot_filename="plots/M_fit.png", result_filename="results/M_parameters.root",
    )
    parameters = parameter_preset("RooBifurGauss")
    parameters[0].update(value=0, minimum=-0.1, maximum=0.1)
    for row in parameters[1:]:
        row.update(value=0.014, minimum=0.008, maximum=0.020)
    nodes["energy"].properties.update(
        expression="deltaE", minimum=-0.3, maximum=0.15,
        model="RooBifurGauss", parameters=parameters,
        use_fit_range=True, fit_minimum=-0.02, fit_maximum=0.02,
        plot_filename="plots/deltaE_fit.png", result_filename="results/deltaE_parameters.root",
        workspace_filename="results/workspace.root",
    )
    execute = project.workflow.add_node(NODE_SPECS["loader_execute"], 100, 100, "Run tau selection")
    execute.properties.update(loader_program=graph.id, argv="./Ntuple")
    return project


if __name__ == "__main__":
    destination = Path(__file__).parent
    project = make_project()
    project.save(destination / "tau_selection.astudio.json")
    graph = next(iter(project.loader_programs.values()))
    (destination / "tau_selection.cc").write_text(generate_loader_cpp(graph), encoding="utf-8")
