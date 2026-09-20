"""Generate illustrative pipelines using the new GUI modules, without ROOT data."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from analysis_studio.model import Project
from analysis_studio.registry import NODE_SPECS
from analysis_studio.codegen import generate_loader_cpp
from analysis_studio.validation import validate_project


def make_project():
    project = Project.empty("Belle tau module examples")
    project.build_options["belle2_analysis_dir"] = "../../Belle2_analysis"

    def program(name, tasks):
        graph = project.create_loader_program(name)
        previous = None
        for index, (kind, props) in enumerate([("loader_decl", {}), *tasks, ("loader_end", {})]):
            node = graph.add_node(NODE_SPECS[kind], (index % 5) * 280, (index // 5) * 180)
            node.properties.update(props)
            if previous:
                graph.add_edge(previous.id, "out", node.id, "in")
            previous = node
        return graph

    def samples(part):
        return [dict(enabled=True, directory=f"Ntuple/{label}/{part}", argument=0, including=".root", label=label, condition="") for label in ("SIGNAL", "BACKGROUND")]

    input_names = [f"extraInfo__bo{index}Muon_cosToThrustOfEvent__bc" for index in ("One", "Two", "Three")]
    preselection = program("selection_modules", [
        ("samples", {"samples": samples("input")}),
        ("sample_roles", {}),
        ("ranked_variables", {"pairs": [dict(rank_expression=f"extraInfo__bo{index}Muon_p__bc", value_expression=f"extraInfo__bo{index}Muon_muonID__bc") for index in ("One", "Two", "Three")],
                              "outputs": [dict(enabled=True, name=f"{name}_muon_muonID", rank=i) for i, name in enumerate(("first", "second", "third"))]}),
        ("variables", {"variables": [dict(enabled=True, name=name, operation=op, expressions=";".join(input_names), order=0) for name, op in (("avg_cosToThrust", "GetAverage"), ("stddev_cosToThrust", "GetStdDev"), ("diff_cosToThrust", "GetDiff"))]}),
        ("cut_flow", {"steps": [dict(enabled=True, name="Mass window", condition="(1.71 < M) && (M < 1.82)", report=True), dict(enabled=True, name="Energy window", condition="(-0.3 < deltaE) && (deltaE < 0.15)", report=True)]}),
        ("candidate_selection", {"mode": "random"}),
        ("candidate_selection", {"mode": "validate"}),
        ("stack_plots", {}),
        ("plots_2d", {}),
        ("profile_fit", {}),
        ("event_split", {"parts": 2, "index": 0}),
        ("root_output", {"directory": "selected/partition_0"}),
    ])
    training = program("train_bdt", [
        ("samples", {"samples": samples("train")}), ("sample_roles", {}),
        ("bdt_train", {"name": "tau.weightfile"}),
    ])
    evaluation = program("evaluate_bdt", [
        ("samples", {"samples": samples("test")}), ("sample_roles", {}),
        ("bdt_apply", {"classifier": "models/tau.weightfile"}),
        ("bdt_evaluate", {"metric": "AUC", "expression": "BDT_output", "output_name": "test_auc"}),
        ("bdt_evaluate", {"metric": "Punzi", "expression": "BDT_output", "output_directory": "plots", "output_name": "test_punzi"}),
    ])
    runs = []
    for index, graph in enumerate((preselection, training, evaluation)):
        run = project.workflow.add_node(NODE_SPECS["loader_execute"], index * 300, 80, graph.name)
        run.properties["loader_program"] = graph.id
        runs.append(run)
    project.workflow.add_edge(runs[1].id, "out", runs[2].id, "in")
    return project


if __name__ == "__main__":
    destination = Path(__file__).parent
    project = make_project()
    errors = validate_project(project, destination)
    if errors:
        raise ValueError("\n".join(errors))
    project.save(destination / "belle_tau_modules.astudio.json")
    for graph in project.loader_programs.values():
        (destination / (graph.name + ".cc")).write_text(generate_loader_cpp(graph), encoding="utf-8")
