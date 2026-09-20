from hashlib import sha1
from pathlib import PurePosixPath

from .recipes import PLOT_COLUMNS, SAMPLE_COLUMNS, row_defaults


def recipe_tag(node):
    return "studio_" + sha1(node.id.encode()).hexdigest()[:12]


def recipe_to_cpp(loader, node):
    from .codegen import cpp_string as q

    p = node.properties
    tag = recipe_tag(node)
    lines = []

    def output_path(filename):
        # filesystem::path handles the execution host's path conventions.
        return f"std::filesystem::path({q(filename)})"

    def mkdir(filename):
        path = output_path(filename)
        lines.append(f"if (!{path}.parent_path().empty()) std::filesystem::create_directories({path}.parent_path());")

    def plots(prefix=""):
        for raw in p.get("plots", []):
            row = {**row_defaults(PLOT_COLUMNS), **raw}
            if not row["enabled"]:
                continue
            filename = str(PurePosixPath(str(p.get("output_directory", "plots"))) / (prefix + str(row["filename"])))
            mkdir(filename)
            args = [q(row['expression']), q(row['title'])]
            if not p.get("automatic_binning", False):
                args += [str(int(float(row['bins']))), str(float(row['minimum'])), str(float(row['maximum']))]
            args += [q(filename), str(bool(p.get("normalized", False))).lower(), str(bool(p.get("log_scale", False))).lower()]
            lines.append(f"{loader}.DrawTH1D({', '.join(args)});")

    if node.type == "samples":
        for raw in p["samples"]:
            row = {**row_defaults(SAMPLE_COLUMNS), **raw}
            if not row["enabled"]:
                continue
            argument = int(float(row["argument"]))
            directory = f"argv[{argument}]" if argument else q(row["directory"])
            if argument:
                lines.append(f'if (argc <= {argument}) {{ fprintf(stderr, "Missing input directory argument {argument}\\n"); return 2; }}')
            condition = str(row["condition"]).strip()
            method = "LoadWithCut" if condition else "Load"
            extra = f", {q(condition)}" if condition else ""
            lines.append(f"{loader}.{method}({directory}, {q(row['including'])}, {q(row['label'])}{extra});")
        return lines
    if node.type == "print_information":
        return [f"{loader}.PrintInformation({q(p['message'])});"]
    if node.type == "plot_set":
        plots()
    elif node.type == "cut_flow":
        for index, row in enumerate(p["steps"], 1):
            if not row.get("enabled", True):
                continue
            if p.get("plot_timing") in {"before", "both"}:
                plots(f"{tag}_{index:02d}_before_")
            lines.append(f"{loader}.Cut({q(row['condition'])});")
            if row.get("report", True):
                lines.append(f"{loader}.PrintInformation({q(row.get('name', '') or row['condition'])});")
            if p.get("plot_timing") in {"after", "both"}:
                plots(f"{tag}_{index:02d}_after_")
    elif node.type == "fit":
        observable, dataset, model, fit = [f"{tag}_{suffix}" for suffix in ("x", "data", "pdf", "fit")]
        lines.append("{")
        lines.append(f"{loader}.DefineObservable({q(observable)}, {q(p['expression'])}, {float(p['minimum'])}, {float(p['maximum'])});")
        if p.get("use_fit_range"):
            lines.append(f"{loader}.SetRange({q(observable)}, {q(tag + '_range')}, {float(p['fit_minimum'])}, {float(p['fit_maximum'])});")
        lines.append(f"{loader}.DefineAndFillDataSet({q(dataset)}, {{{q(observable)}}}, {{{q(p['expression'])}}});")
        parameters = []
        for index, row in enumerate(p["parameters"]):
            name = f"{tag}_p{index}_{row['name']}"
            parameters.append(q(name))
            if row.get("constant", False):
                lines.append(f"{loader}.DefineConstantParameter({q(name)}, {q(row['name'])}, {float(row['value'])});")
            else:
                lines.append(f"{loader}.DefineFitParameter({q(name)}, {q(row['name'])}, {float(row['value'])}, {float(row['minimum'])}, {float(row['maximum'])});")
        lines.append(f"{loader}.DefineModel({q(model)}, {q(p['model'])}, {{{q(observable)}}}, {{{', '.join(parameters)}}});")
        lines.extend(["FitOptions fit_options;", f"fit_options.strategy = {int(p['strategy'])};",
                      f"fit_options.SumW2Error = {str(bool(p['sumw2'])).lower()};",
                      ])
        if p.get("use_fit_range"):
            lines.append(f"fit_options.range = {q(tag + '_range')};")
        lines.append(f"{loader}.Fit({q(fit)}, {q(dataset)}, {q(model)}, fit_options);")
        if p.get("plot_filename"):
            mkdir(p["plot_filename"])
            lines.extend(["FitPlotOptions plot_options;", f"plot_options.bins = {int(float(p['bins']))};",
                          f"plot_options.sumW2Error = {str(bool(p['sumw2'])).lower()};",
                          "plot_options.showFitparam = true;",
                          f"{loader}.PlotFit({q(fit)}, {q(observable)}, {q(p['plot_filename'])}, plot_options);"])
        if p.get("result_filename"):
            mkdir(p["result_filename"])
            lines.append(f"{loader}.ExportFitResult({q(p['result_filename'])}, {{{q(fit)}}});")
        if p.get("workspace_filename"):
            mkdir(p["workspace_filename"])
            lines.append(f"{loader}.SaveWorkspace({q(p['workspace_filename'])});")
        lines.append("}")
    return lines
