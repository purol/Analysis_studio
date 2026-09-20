import math
import re
from pathlib import PurePosixPath, PureWindowsPath

from .analysis_modules import MODULE_SPECS, active_rows, values, support_files
from .recipes import row_defaults

SYMBOL = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:::[A-Za-z_][A-Za-z0-9_]*)*")


def validate_module(node):
    if node.type not in MODULE_SPECS:
        return []
    p, errors = node.properties, []

    def fail(where, message):
        errors.append(f"{node.title} / {where}: {message}")

    def fields(properties, data, where):
        for prop in properties:
            value = data.get(prop.name, prop.default)
            location = f"{where} {prop.label}".strip()
            if prop.kind in {"int", "float"}:
                try:
                    number = float(value)
                    if isinstance(value, bool) or not math.isfinite(number) or prop.kind == "int" and number != int(number):
                        raise ValueError()
                except (ValueError, TypeError, OverflowError):
                    fail(location, f"enter a finite {prop.kind} value.")
            elif prop.kind == "bool" and not isinstance(value, bool):
                fail(location, "expected true or false.")
            elif prop.kind == "choice" and value not in prop.choices:
                fail(location, "choose one of: " + ", ".join(prop.choices))
            elif prop.kind == "table":
                if not isinstance(value, list):
                    fail(location, "expected table rows.")
                    continue
                for index, row in enumerate(value, 1):
                    if not isinstance(row, dict):
                        fail(location, f"row {index} is not a table row.")
                    elif row.get("enabled", True):
                        fields(prop.columns, row, f"{location} row {index}")

    fields(MODULE_SPECS[node.type].properties, p, "")
    if errors:
        return errors

    def require(data, names, where=""):
        for name in names:
            if not str(data.get(name, "")).strip():
                fail(where or name, f"{name} must not be empty.")

    def bounds(row, low="minimum", high="maximum", where="range"):
        if float(row[low]) >= float(row[high]):
            fail(where, f"{low} must be smaller than {high}.")

    def positive(data, key, where=""):
        if float(data[key]) <= 0:
            fail(where or key, f"{key} must be positive.")

    def rows(key, required=True):
        result = active_rows(node, key)
        if required and not result:
            fail(key, "add at least one active row.")
        return result

    def unique(records, key, where):
        seen = set()
        for row in records:
            value = str(row[key]).strip()
            if value in seen:
                fail(where, f"duplicate {key}: {value}.")
            seen.add(value)

    for key in {"header", "callback", "weight_object"} & p.keys():
        value = str(p[key]).strip()
        if value and key in {"callback", "weight_object"} and not SYMBOL.fullmatch(value):
            fail(key, "enter a C++ symbol, optionally namespace-qualified.")

    if node.type == "sample_roles":
        for a, b in (("mc", "data"), ("signal", "background")):
            if set(values(p[a])) & set(values(p[b])):
                fail("labels", f"{a} and {b} must not overlap.")
    elif node.type == "event_weight":
        require(p, ["weight"])
        mapping = rows("mapping", required=False)
        for row in mapping:
            require(row, ["argument", "expression"], "mapping")
        unique(mapping, "argument", "mapping")
    elif node.type == "variables":
        records = rows("variables")
        unique(records, "name", "variables")
        for row in records:
            require(row, ["name", "expressions"], row["name"])
            inputs = values(row["expressions"])
            if row["operation"] in {"GetDiff", "GetAdd"}:
                combinations = len(inputs) * (len(inputs) - 1) // 2
                if not 0 <= float(row["order"]) < combinations:
                    fail(row["name"], "pair order is outside the available input pairs.")
    elif node.type == "ranked_variables":
        pairs, outputs = rows("pairs"), rows("outputs")
        unique(pairs, "rank_expression", "pairs")
        unique(outputs, "name", "outputs")
        for row in pairs:
            require(row, ["rank_expression", "value_expression"], "pairs")
        for row in outputs:
            require(row, ["name"], "outputs")
            if not 0 <= float(row["rank"]) < len(pairs):
                fail(row["name"], "rank is outside the ranking pairs.")
    elif node.type in {"remove_variables", "print_events", "bdt_train", "bdt_apply"}:
        require(p, ["variables"])
    if node.type in {"candidate_selection", "event_split"}:
        require(p, ["event_keys"])
        if node.type == "event_split":
            positive(p, "parts")
            if not 0 <= float(p["index"]) < float(p["parts"]):
                fail("index", "partition must be in [0, number of partitions).")
        elif p["mode"] == "best":
            require(p, ["expression"])
    if node.type in {"stack_plots", "plots_2d"}:
        plots = rows("plots")
        unique(plots, "filename", "plots")
        for row in plots:
            filename = str(row["filename"])
            if not filename or filename in {".", ".."} or any(c in filename for c in "/\\:"):
                fail("plots", "enter a filename without directories.")
            if node.type == "stack_plots":
                require(row, ["expression"], "plots")
                if not p["automatic_binning"]:
                    bounds(row)
                    positive(row, "bins")
            else:
                require(row, ["x", "y"], "plots")
                if not p["automatic_binning"]:
                    for axis in ("x", "y"):
                        bounds(row, axis + "_min", axis + "_max", "plots")
                        positive(row, axis + "_bins")
    if node.type == "root_output":
        require(p, ["filename"] if p["mode"] == "combined" else ["directory"])
        for key in ("prefix", "suffix"):
            if any(c in str(p[key]) for c in "/\\"):
                fail(key, "prefix/suffix must not contain directory separators.")
    if node.type == "bdt_train":
        require(p, ["directory"])
        hyper = rows("hyperparameters", required=False)
        unique(hyper, "name", "hyperparameters")
        for row in hyper:
            key, value = row["name"], float(row["value"])
            if key not in {"NTrees", "Depth", "Shrinkage", "Subsample", "Binning"}:
                fail("hyperparameters", f"unsupported hyperparameter {key}.")
            elif value <= 0 or key in {"NTrees", "Depth", "Binning"} and value != int(value):
                fail(key, "enter a positive value (integer for NTrees, Depth and Binning).")
            elif key in {"Shrinkage", "Subsample"} and value > 1:
                fail(key, "value must be in (0, 1].")
    if node.type == "bdt_apply":
        require(p, ["classifier", "branch"])
    if node.type == "bdt_evaluate":
        require(p, ["expression"])
        if "filename" in p:
            require(p, ["filename"])
        else:
            require(p, ["output_directory", "output_name"])
            name = str(p.get("output_name", "")).strip()
            if name.lower() in {".", "..", ".png", ".txt"} or any(c in name for c in '/\\:\n\r'):
                fail("output_name", "enter a filename only; put directories in Output folder.")
        bounds(p)
        if p["metric"] != "AUC":
            positive(p, "bins")
            if float(p["rank"]) < 0:
                fail("rank", "rank must not be negative.")
        if p["metric"] == "Punzi":
            positive(p, "initial_signal")
            positive(p, "alpha")
    if node.type == "histogram":
        require(p, ["name", "filename", "expressions" if p["callback"] else "expression"])
        bounds(p)
        positive(p, "bins")
    if node.type == "dataset":
        require(p, ["name", "filename"])
        observables = rows("observables")
        unique(observables, "name", "observables")
        for row in observables:
            require(row, ["name", "expression"], "observables")
            bounds(row)
            if row["name"] == "weight":
                fail("observables", "weight is reserved for the event weight.")
    if node.type == "profile":
        require(p, ["name", "x", "y", "filename"])
        bounds(p)
        bounds(p, "y_min", "y_max")
        positive(p, "bins")
    if node.type == "profile_fit":
        require(p, ["x", "y", "formula"])
        bounds(p)
        bounds(p, "y_min", "y_max")
        positive(p, "bins")
        parameters = rows("parameters", required=False)
        unique(parameters, "name", "parameters")
        for row in parameters:
            require(row, ["name"], "parameters")
            if not row["constant"]:
                bounds(row)
                if not float(row["minimum"]) <= float(row["value"]) <= float(row["maximum"]):
                    fail(row["name"], "initial value must lie inside its bounds.")
        indexes = [int(i) for i in re.findall(r"\[(\d+)\]", p["formula"])]
        if indexes and max(indexes) >= len(parameters):
            fail("formula", "TF1 references a parameter index not present in the table.")
    return errors


def validate_support_files(graph, directory=None):
    errors = []
    for kind in ("headers", "sources"):
        for filename in support_files(graph, kind):
            path = PurePosixPath(filename.replace("\\", "/"))
            if path.is_absolute() or PureWindowsPath(filename).drive or ".." in path.parts or any(c in filename for c in '\n\r"<>'):
                errors.append(f"{graph.name}: support file must be project-relative: {filename}")
            elif directory is not None and not (directory / filename).is_file():
                errors.append(f"{graph.name}: support file does not exist: {filename}")
    return errors
