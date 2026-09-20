"""Public Loader API adapters; ROOT object exports are deferred until end()."""
from pathlib import PurePosixPath

from .analysis_modules import active_rows, values
from .recipe_codegen import recipe_tag


def module_to_cpp(loader, node, after=False, register_weight=True):
    from .codegen import cpp_string as q

    p, tag = node.properties, recipe_tag(node)
    lines = []
    boolean = lambda value: "true" if value else "false"
    number = lambda value: repr(float(value))
    integer = lambda value: str(int(float(value)))
    vector = lambda value: "{" + ", ".join(q(v) for v in values(value)) + "}"

    def call(method, *arguments):
        lines.append(f"{loader}.{method}({', '.join(arguments)});")

    def mkdir(filename, directory=False):
        path = f"std::filesystem::path({q(filename)})" + ("" if directory else ".parent_path()")
        lines.append(f"if (!{path}.empty()) std::filesystem::create_directories({path});")

    def save_object():
        mkdir(p["filename"])
        lines.extend(["{", f"TFile output({q(p['filename'])}, \"RECREATE\");",
                      'if (output.IsZombie()) throw std::runtime_error("Cannot create ROOT output");',
                      f"{tag}.Write();", "}"])

    if after:
        if node.type in {"histogram", "dataset", "profile"}:
            save_object()
        elif node.type == "cpp_support":
            lines.extend(str(p["after"]).splitlines())
        return lines

    if node.type == "cpp_support":
        return str(p["setup"]).splitlines()
    if node.type == "sample_roles":
        for key, method in (("mc", "SetMC"), ("data", "SetData"), ("signal", "SetSignal"), ("background", "SetBackground")):
            call(method, vector(p[key]))
    elif node.type == "event_weight":
        if p["weight_object"] and register_weight:
            lines.append(f"EventWeights::Register({q(p['weight'])}, {p['weight_object']});")
        pairs = ", ".join("{" + q(r["argument"]) + ", " + q(r["expression"]) + "}" for r in active_rows(node, "mapping"))
        call("AddWeight", q(p["weight"]), "{" + pairs + "}")
    elif node.type == "variables":
        for row in active_rows(node, "variables"):
            operation = row["operation"]
            inputs = q(row["expressions"]) if operation == "DefineNewVariable" else vector(row["expressions"])
            args = [inputs]
            if operation in {"GetDiff", "GetAdd"}:
                args.append(integer(row["order"]))
            call(operation, *args, q(row["name"]))
    elif node.type == "ranked_variables":
        pairs = ", ".join("{" + q(r["rank_expression"]) + ", " + q(r["value_expression"]) + "}" for r in active_rows(node, "pairs"))
        for row in active_rows(node, "outputs"):
            call("ConditionalPairDefineNewVariable", "{" + pairs + "}", integer(row["rank"]), q(row["name"]))
    elif node.type == "remove_variables":
        call("RemoveVariable", vector(p["variables"]))
    elif node.type == "candidate_selection":
        if p["mode"] == "best":
            call("BCS", q(p["expression"]), q(p["criteria"]), vector(p["event_keys"]))
        else:
            call("RandomBCS" if p["mode"] == "random" else "IsBCSValid", vector(p["event_keys"]))
    elif node.type == "event_split":
        call("RandomEventSelection", integer(p["parts"]), integer(p["index"]), vector(p["event_keys"]))
    elif node.type in {"stack_plots", "plots_2d"}:
        for row in active_rows(node, "plots"):
            filename = str(PurePosixPath(p["output_directory"]) / row["filename"])
            mkdir(filename)
            if node.type == "stack_plots":
                args = [q(row["expression"]), q(row["title"])]
                if not p["automatic_binning"]:
                    args += [integer(row["bins"]), number(row["minimum"]), number(row["maximum"])]
                call("DrawStack", *args, q(filename), boolean(p["normalized"]), boolean(p["log_scale"]))
            else:
                args = [q(row["x"]), q(row["y"]), q(row["title"])]
                if not p["automatic_binning"]:
                    for axis in ("x", "y"):
                        args += [integer(row[axis + "_bins"]), number(row[axis + "_min"]), number(row[axis + "_max"])]
                call("DrawTH2D", *args, q(filename), q(row["option"]))
    elif node.type == "root_output":
        if p["mode"] == "separate":
            mkdir(p["directory"], directory=True)
            call("PrintSeparateRootFile", q(p["directory"]), q(p["prefix"]), q(p["suffix"]))
        else:
            mkdir(p["filename"])
            call("PrintRootFile", q(p["filename"]))
    elif node.type == "print_events":
        call("PrintEvent", vector(p["variables"]))
    elif node.type == "bdt_train":
        mkdir(p["directory"], directory=True)
        hyper = ", ".join("{" + q(r["name"]) + ", " + number(r["value"]) + "}" for r in active_rows(node, "hyperparameters"))
        call("FastBDTTrain", vector(p["variables"]), q(p["signal_cut"]), q(p["background_cut"]), "{" + hyper + "}", boolean(p["balanced"]), q(p["directory"]), q(p["name"]))
    elif node.type == "bdt_apply":
        call("FastBDTApplication", vector(p["variables"]), q(p["classifier"]), q(p["branch"]))
    elif node.type == "bdt_evaluate":
        mkdir(p["filename"])
        args = [q(p["expression"]), number(p["minimum"]), number(p["maximum"])]
        if p["metric"] == "AUC":
            call("CalculateAUC", *args, q(p["filename"]), q(p["write_mode"]))
        else:
            args.append(integer(p["bins"]))
            if p["metric"] == "Punzi":
                args += [number(p["initial_signal"]), number(p["alpha"])]
            call("DrawPunziFOM" if p["metric"] == "Punzi" else "DrawFOM", *args, integer(p["rank"]), q(p["filename"]))
    elif node.type == "histogram":
        lines.extend([f"TH1D {tag}({q(p['name'])}, {q(p['title'])}, {integer(p['bins'])}, {number(p['minimum'])}, {number(p['maximum'])});",
                      f"{tag}.SetDirectory(nullptr);", f"{tag}.Sumw2();"])
        if p["callback"]:
            call("FillCustomizedTH1D", "&" + tag, vector(p["expressions"]), str(p["callback"]))
        else:
            call("FillTH1D", "&" + tag, q(p["expression"]))
    elif node.type == "dataset":
        args = []
        expressions = []
        for index, row in enumerate(active_rows(node, "observables")):
            variable = f"{tag}_x{index}"
            lines.append(f"RooRealVar {variable}({q(row['name'])}, {q(row['name'])}, {number(row['minimum'])}, {number(row['maximum'])});")
            args.append(variable)
            expressions.append(q(row["expression"]))
        lines.extend([f'RooRealVar {tag}_weight("weight", "weight", 1.0);', f"RooArgSet {tag}_columns;"])
        for variable in [*args, tag + "_weight"]:
            lines.append(f"{tag}_columns.add({variable});")
        lines.append(f'RooDataSet {tag}({q(p["name"])}, {q(p["name"])}, {tag}_columns, RooFit::WeightVar("weight"));')
        call("FillDataSet", "&" + tag, "{" + ", ".join("&" + v for v in args) + "}", "{" + ", ".join(expressions) + "}")
    elif node.type == "profile":
        lines.extend([f"TProfile {tag}({q(p['name'])}, {q(';' + p['x'] + ';' + p['y'])}, {integer(p['bins'])}, {number(p['minimum'])}, {number(p['maximum'])}, {number(p['y_min'])}, {number(p['y_max'])});",
                      f"{tag}.SetDirectory(nullptr);"])
        call("FillTProfile", "&" + tag, q(p["x"]), q(p["y"]))
    elif node.type == "profile_fit":
        profile, model, fit = (q(tag + s) for s in ("_profile", "_model", "_fit"))
        call("DefineAndFillProfile", profile, q(";" + p["x"] + ";" + p["y"]), integer(p["bins"]), number(p["minimum"]), number(p["maximum"]), number(p["y_min"]), number(p["y_max"]), q(p["x"]), q(p["y"]))
        params = ", ".join("{" + ", ".join([q(r["name"]), number(r["value"]), number(r["minimum"]), number(r["maximum"]), boolean(r["constant"])]) + "}" for r in active_rows(node, "parameters"))
        call("DefineTF1", model, q(p["formula"]), number(p["minimum"]), number(p["maximum"]), "{" + params + "}")
        call("Fit", fit, profile, model)
        if p["plot_filename"]:
            mkdir(p["plot_filename"])
            call("PlotFit", fit, q(""), q(p["plot_filename"]))
        if p["result_filename"]:
            mkdir(p["result_filename"])
            call("ExportFitResult", q(p["result_filename"]), "{" + fit + "}")
        if p["workspace_filename"]:
            mkdir(p["workspace_filename"])
            call("SaveWorkspace", q(p["workspace_filename"]))
    else:
        raise ValueError(f"No module generator for {node.type}")
    return lines
