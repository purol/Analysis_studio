"""Task-oriented modules based on the Loader calls in Belle_tau/analysis_code/src.

Keep these schemas independent of Qt, so CLI generation uses the same contract.
"""
from .model import NodeSpec, PropertySpec as P
from .recipes import PLOT_COLUMNS, PARAMETER_COLUMNS, row_defaults


def table(name, label, columns, rows=None, help=""):
    return P(name, label, "table", [row_defaults(columns)] if rows is None else rows,
             columns=columns, help=help)


def strings(name, label, default="", help="One item per line."):
    return P(name, label, "text", default, multiline=True, help=help)


ENABLED = P("enabled", "Use", "bool", True)
EVENT_KEYS = "__experiment__\n__run__\n__event__\n__production__\n__ncandidates__"
VARIABLE_COLUMNS = (
    ENABLED, P("name", "Output variable", default="new_variable"),
    P("operation", "Operation", "choice", "DefineNewVariable",
      ("DefineNewVariable", "GetAverage", "GetStdDev", "GetDiff", "GetAdd", "GetRandom")),
    P("expressions", "Expression(s); separate with ;", default="x + y"),
    P("order", "Order (Diff/Add)", "int", 0),
)
PAIR_COLUMNS = (P("rank_expression", "Ranking expression", default="muon_p"),
                P("value_expression", "Value expression", default="muon_muonID"))
RANK_COLUMNS = (ENABLED, P("name", "Output variable", default="first_muon_muonID"),
                P("rank", "Rank (0 = highest)", "int", 0))
WEIGHT_COLUMNS = (P("argument", "Weight input name", default="momentum"),
                  P("expression", "Analysis variable / expression", default="first_muon_p"))
PLOT2D_COLUMNS = (
    ENABLED, P("x", "X expression", default="M"), P("y", "Y expression", default="deltaE"),
    P("title", "Axis title", default=";M [GeV];deltaE [GeV]"),
    P("x_bins", "X bins", "int", 50), P("x_min", "X min", "float", 1.5), P("x_max", "X max", "float", 1.9),
    P("y_bins", "Y bins", "int", 50), P("y_min", "Y min", "float", -0.3), P("y_max", "Y max", "float", 0.15),
    P("filename", "Filename", default="M_deltaE.png"), P("option", "Draw option", default="COLZ"),
)
OBSERVABLE_COLUMNS = (
    P("name", "Observable", default="M"), P("expression", "Expression", default="M"),
    P("minimum", "Min", "float", 1.5), P("maximum", "Max", "float", 1.9),
)
HYPER_COLUMNS = (P("name", "Hyperparameter", default="NTrees"), P("value", "Value", "float", 100))


def module_specs():
    def spec(key, label, category, *properties):
        return NodeSpec(key, label, category, "loader", "#537e9b", properties=properties)

    return [
        spec("sample_roles", "Sample Roles", "Samples & weights",
             strings("mc", "MC labels", "SIGNAL\nBACKGROUND"), strings("data", "Data labels"),
             strings("signal", "Signal labels", "SIGNAL"), strings("background", "Background labels", "BACKGROUND")),
        spec("event_weight", "Event Weight", "Samples & weights",
             P("weight", "Registered weight name", default="MC_weight"),
             P("weight_object", "C++ EventWeight object (blank if registered)", default=""),
             P("header", "Weight header (project-relative)", "path", ""),
             table("mapping", "Weight inputs", WEIGHT_COLUMNS, [],
                   help="Map the weight's input names to analysis expressions. Supply an EventWeight object symbol and its header to register it, or register it in C++ Support. Repeated identical registrations are emitted once per program.")),
        spec("variables", "Variables", "Transform",
             table("variables", "Calculated variables", VARIABLE_COLUMNS,
                   help="Rows execute in order. Aggregate inputs are separated by semicolons. Diff/Add order follows the framework's zero-based ranking.")),
        spec("ranked_variables", "Ranked Variables", "Transform",
             table("pairs", "Ranking / value pairs", PAIR_COLUMNS),
             table("outputs", "Ranked outputs", RANK_COLUMNS)),
        spec("remove_variables", "Remove Variables", "Transform", strings("variables", "Variables to remove", "unused_variable")),
        spec("candidate_selection", "Candidate Selection", "Selection",
             P("mode", "Action", "choice", "random", ("random", "best", "validate")),
             P("expression", "Best-candidate expression", default="chiProb"),
             P("criteria", "Keep", "choice", "highest", ("highest", "lowest")),
             strings("event_keys", "Event identity columns", EVENT_KEYS)),
        spec("event_split", "Event Split", "Selection",
             P("parts", "Number of partitions", "int", 2), P("index", "Selected partition (0-based)", "int", 0),
             strings("event_keys", "Event identity columns", EVENT_KEYS)),
        spec("stack_plots", "Stack Plot Set", "Plot", table("plots", "Plots", PLOT_COLUMNS),
             P("output_directory", "Plot directory", "path", "plots"),
             P("automatic_binning", "Use framework binning", "bool", False),
             P("normalized", "Normalize", "bool", False), P("log_scale", "Log scale", "bool", False)),
        spec("plots_2d", "2D Plot Set", "Plot", table("plots", "Plots", PLOT2D_COLUMNS),
             P("output_directory", "Plot directory", "path", "plots"),
             P("automatic_binning", "Use framework binning", "bool", False)),
        spec("root_output", "ROOT Output", "Output",
             P("mode", "Output mode", "choice", "separate", ("separate", "combined")),
             P("directory", "Output directory", "path", "selected"),
             P("prefix", "Filename prefix", default=""), P("suffix", "Filename suffix", default=""),
             P("filename", "Combined ROOT file", "path", "selected.root")),
        spec("print_events", "Print Events", "Output", strings("variables", "Variables to print", "M\ndeltaE")),
        spec("bdt_train", "Train FastBDT", "BDT",
             strings("variables", "Input variables", "M\ndeltaE"),
             P("signal_cut", "Signal preselection", default=""), P("background_cut", "Background preselection", default=""),
             table("hyperparameters", "Hyperparameters", HYPER_COLUMNS,
                   [dict(name=k, value=v) for k, v in (("NTrees", 100), ("Depth", 3), ("Shrinkage", 0.1), ("Subsample", 0.5), ("Binning", 8))]),
             P("balanced", "Balance signal/background weights", "bool", True),
             P("directory", "Model directory", "path", "models"), P("name", "Model name (blank = automatic)", default="classifier.weightfile")),
        spec("bdt_apply", "Apply FastBDT", "BDT",
             strings("variables", "Inputs (training order)", "M\ndeltaE"),
             P("classifier", "Classifier file", "path", "models/classifier.weightfile"),
             P("branch", "Output variable", default="BDT_output")),
        spec("bdt_evaluate", "BDT Performance", "BDT",
             P("metric", "Metric", "choice", "Punzi", ("Punzi", "FOM", "AUC")),
             P("expression", "Classifier expression", default="BDT_output"),
             P("minimum", "Scan min", "float", 0.0), P("maximum", "Scan max", "float", 1.0),
             P("bins", "Scan bins", "int", 100), P("rank", "Cut rank", "int", 1),
             P("initial_signal", "Initial signal yield", "float", 1000.0), P("alpha", "Punzi alpha", "float", 1.28),
             P("filename", "Output (PNG or AUC text)", "path", "results/Punzi.png"),
             P("write_mode", "AUC write mode", "choice", "w", ("w", "a"))),
        spec("histogram", "ROOT Histogram", "Output",
             P("name", "ROOT object name", default="histogram"), P("title", "Title", default=";M;Events"),
             P("bins", "Bins", "int", 50), P("minimum", "Min", "float", 1.5), P("maximum", "Max", "float", 1.9),
             P("expression", "Expression", default="M"),
             P("callback", "Custom C++ mapping function (optional)", default=""),
             P("header", "Callback header (project-relative)", "path", ""),
             strings("expressions", "Custom mapping inputs", "M\ndeltaE"),
             P("filename", "Output ROOT file", "path", "results/histogram.root")),
        spec("dataset", "RooDataSet Output", "Output",
             P("name", "Dataset name", default="dataset"), table("observables", "Observables", OBSERVABLE_COLUMNS),
             P("filename", "Output ROOT file", "path", "results/dataset.root")),
        spec("profile", "ROOT Profile", "Output",
             P("name", "ROOT object name", default="profile"),
             P("x", "X expression", default="deltaE"), P("y", "Y expression", default="M"),
             P("bins", "Bins", "int", 100),
             P("minimum", "X min", "float", -0.3), P("maximum", "X max", "float", 0.15),
             P("y_min", "Y min", "float", 1.71), P("y_max", "Y max", "float", 1.82),
             P("filename", "Output ROOT file", "path", "results/profile.root")),
        spec("profile_fit", "Profile Fit", "Fit",
             P("x", "X expression", default="deltaE"), P("y", "Y expression", default="M"),
             P("bins", "Profile bins", "int", 100),
             P("minimum", "X min", "float", -0.3), P("maximum", "X max", "float", 0.15),
             P("y_min", "Y min", "float", 1.71), P("y_max", "Y max", "float", 1.82),
             P("formula", "TF1 formula", default="[0] + [1]*x"),
             table("parameters", "TF1 parameters (index order)", PARAMETER_COLUMNS,
                   [dict(name="intercept", value=1.777, minimum=1.7, maximum=1.85, constant=False),
                    dict(name="slope", value=0, minimum=-1, maximum=1, constant=False)]),
             P("plot_filename", "Fit plot (blank to skip)", "path", "plots/profile_fit.png"),
             P("result_filename", "Parameters ROOT file (blank to skip)", "path", "results/profile_parameters.root"),
             P("workspace_filename", "Workspace (blank to skip)", "path", "")),
        spec("cpp_support", "C++ Support", "Advanced",
             strings("headers", "Project-relative headers"), strings("sources", "Additional C++ sources"),
             strings("declarations", "Global declarations / functions"), strings("setup", "Statements at this position"),
             strings("after", "Statements after this Loader's end()", help="Runs after the connected Loader finishes, when filled ROOT objects and returned module results are available.")),
    ]


MODULE_SPECS = {s.key: s for s in module_specs()}


def values(value):
    """Newline or semicolon separated expressions; commas can be function arguments."""
    return [v.strip() for v in str(value).replace(";", "\n").splitlines() if v.strip()]


def active_rows(node, name):
    prop = next(p for p in MODULE_SPECS[node.type].properties if p.name == name)
    return [{**row_defaults(prop.columns), **r} for r in node.properties[name] if r.get("enabled", True)]


def support_files(graph, kind):
    result = []
    for node in graph.nodes:
        if node.type == "cpp_support":
            result.extend(values(node.properties.get(kind, "")))
        elif kind == "headers" and node.type in {"event_weight", "histogram"}:
            if node.properties.get("header"):
                result.append(str(node.properties["header"]))
    return list(dict.fromkeys(result))
