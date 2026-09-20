"""Declarative, Qt-free schemas for common analysis tasks."""
from .model import NodeSpec, PropertySpec as P

CUT_COLUMNS = (
    P("enabled", "Use", "bool", True),
    P("name", "Stage", default="Selection"),
    P("condition", "Cut expression", default="M > 1.5"),
    P("report", "Print information", "bool", True),
)
PLOT_COLUMNS = (
    P("enabled", "Use", "bool", True),
    P("expression", "Expression", default="M"),
    P("title", "Axis title", default=";M [GeV];Events"),
    P("bins", "Bins", "int", 50),
    P("minimum", "Min", "float", 1.5),
    P("maximum", "Max", "float", 1.9),
    P("filename", "Filename", default="M.png"),
)
PARAMETER_COLUMNS = (
    P("name", "Parameter", default="mean"),
    P("value", "Initial value", "float", 1.777),
    P("minimum", "Min", "float", 1.7),
    P("maximum", "Max", "float", 1.85),
    P("constant", "Fixed", "bool", False),
)
SAMPLE_COLUMNS = (
    P("enabled", "Use", "bool", True),
    P("directory", "Directory", default="./Ntuple"),
    P("argument", "Directory argv (0 = path)", "int", 0),
    P("including", "Filename contains", default=".root"),
    P("label", "Sample label", default="SIGNAL"),
    P("condition", "Initial cut (optional)", default=""),
)

def row_defaults(columns):
    return {c.name: c.default for c in columns}

MODEL_PARAMETERS = {
    "RooGaussian": ("mean", "sigma"),
    "RooBifurGauss": ("mean", "sigma_left", "sigma_right"),
    "RooExponential": ("slope",),
    "RooBreitWigner": ("mean", "width"),
}

def parameter_preset(model):
    result = []
    for name in MODEL_PARAMETERS[model]:
        value, low, high = ((1.777, 1.7, 1.85) if name == "mean" else
                            (-1.0, -100.0, 0.0) if name == "slope" else
                            (0.005, 0.00001, 0.1))
        result.append(dict(name=name, value=value, minimum=low, maximum=high, constant=False))
    return result

def recipe_specs():
    plots = P("plots", "Plots", "table", [row_defaults(PLOT_COLUMNS)], columns=PLOT_COLUMNS)
    plot_options = (
        P("automatic_binning", "Use framework binning", "bool", False),
        P("normalized", "Normalize plots", "bool", False),
        P("log_scale", "Log scale", "bool", False),
    )
    return [
        NodeSpec("samples", "Samples", "Input", "loader", "#4263a8", properties=(
            P("samples", "Input samples", "table", [row_defaults(SAMPLE_COLUMNS)], columns=SAMPLE_COLUMNS,
              help="Enter paths without C++ quotes. To use a runtime directory, set its argv number (1 = first argument). Filename contains is a substring, not a glob."),
        )),
        NodeSpec("cut_flow", "Cut Flow", "Selection", "loader", "#a06c2b", properties=(
            P("steps", "Selection stages", "table", [row_defaults(CUT_COLUMNS)], columns=CUT_COLUMNS,
              help="Rows run from top to bottom. Disable a stage to skip its cut and diagnostics."),
            P("plot_timing", "Plots at each stage", "choice", "none", ("none", "before", "after", "both")),
            plots,
            P("output_directory", "Plot directory", "path", "plots"),
            *plot_options,
        )),
        NodeSpec("plot_set", "Plot Set", "Plot", "loader", "#9a4d64", properties=(
            plots, P("output_directory", "Plot directory", "path", "plots"), *plot_options,
        )),
        NodeSpec("print_information", "Print Information", "Output", "loader", "#9a4d64", properties=(
            P("message", "Message", default="Selection summary"),
        )),
        NodeSpec("fit", "Fit", "Fit", "loader", "#7651a8", properties=(
            P("expression", "Observable expression", default="M"),
            P("minimum", "Observable min", "float", 1.7),
            P("maximum", "Observable max", "float", 1.85),
            P("model", "PDF model", "choice", "RooGaussian", tuple(MODEL_PARAMETERS)),
            P("parameters", "Parameters (PDF argument order)", "table", parameter_preset("RooGaussian"),
              columns=PARAMETER_COLUMNS, help="Use the model preset button to replace the rows. Adjust values and bounds for your observable."),
            P("sumw2", "Weighted-data errors (SumW2)", "bool", True),
            P("use_fit_range", "Fit a restricted range", "bool", False),
            P("fit_minimum", "Fit range min", "float", 1.77),
            P("fit_maximum", "Fit range max", "float", 1.785),
            P("strategy", "Minimizer strategy", "choice", "1", ("0", "1", "2")),
            P("bins", "Plot bins", "int", 100),
            P("plot_filename", "Fit plot (blank to skip)", default="fit.png"),
            P("workspace_filename", "Workspace (blank to skip)", default=""),
            P("result_filename", "Fit parameters (blank to skip)", default=""),
        )),
    ]


def add_analysis_starter(graph):
    """A connected GUI starting point; its initial selection accepts all events."""
    from .registry import NODE_SPECS
    previous = None
    for index, (kind, title) in enumerate((("loader_decl", "Loader Declaration"),
                                          ("samples", "Input samples"),
                                          ("cut_flow", "Selections"),
                                          ("loader_end", "Run analysis"))):
        node = graph.add_node(NODE_SPECS[kind], index * 290, 60, title)
        if kind == "cut_flow":
            node.properties["steps"] = [dict(enabled=True, name="All events", condition="1", report=True)]
        if previous:
            graph.add_edge(previous.id, "out", node.id, "in")
        previous = node
