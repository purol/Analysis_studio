import math
from pathlib import PurePosixPath

from .recipes import MODEL_PARAMETERS, PLOT_COLUMNS, SAMPLE_COLUMNS, row_defaults


def validate_recipe(node):
    """Validate edited/loaded rows before code generation; report row locations."""
    errors = []
    p = node.properties

    def fail(where, message):
        errors.append(f"{node.title} / {where}: {message}")

    def number(row, key, where, positive=False, integer=False):
        try:
            value = float(row[key])
            if isinstance(row[key], bool) or not math.isfinite(value):
                raise ValueError()
            if positive and value <= 0 or integer and value != int(value):
                raise ValueError()
            return value
        except (KeyError, TypeError, ValueError, OverflowError):
            fail(where, f"{key} must be a finite {'positive ' if positive else ''}{'integer' if integer else 'number'}.")
            return None

    def bounds(row, where):
        low, high = number(row, "minimum", where), number(row, "maximum", where)
        if low is not None and high is not None and low >= high:
            fail(where, "minimum must be smaller than maximum.")
        return low, high

    def rows(key):
        value = p.get(key)
        if not isinstance(value, list) or not value:
            fail(key, "add at least one row.")
            return []
        result = []
        for i, row in enumerate(value, 1):
            where = f"{key} row {i}"
            if not isinstance(row, dict):
                fail(where, "expected a table row.")
                continue
            for key_bool in ("enabled", "report", "constant"):
                if key_bool in row and not isinstance(row[key_bool], bool):
                    fail(where, f"{key_bool} must be true or false.")
            if key == "parameters" or row.get("enabled", True):
                result.append((where, row))
        return result

    if node.type == "samples":
        for where, raw in rows("samples"):
            row = {**row_defaults(SAMPLE_COLUMNS), **raw}
            argument = number(row, "argument", where, integer=True)
            if argument is not None and not 0 <= argument <= 10000:
                fail(where, "argv number must be between 0 and 10000.")
            if not argument and not str(row["directory"]).strip():
                fail(where, "directory is empty.")
            if not str(row["label"]).strip():
                fail(where, "sample label is empty.")
    if node.type == "cut_flow":
        if p.get("plot_timing") not in {"none", "before", "after", "both"}:
            fail("plots", "choose none, before, after or both.")
        for where, row in rows("steps"):
            if not str(row.get("condition", "")).strip():
                fail(where, "cut expression is empty.")
    if node.type == "plot_set" or node.type == "cut_flow" and p.get("plot_timing") != "none":
        filenames = set()
        for where, raw in rows("plots"):
            row = {**row_defaults(PLOT_COLUMNS), **raw}
            if not p.get("automatic_binning", False):
                bounds(row, where)
                number(row, "bins", where, positive=True, integer=True)
            if not str(row.get("expression", "")).strip():
                fail(where, "plot expression is empty.")
            filename = str(row.get("filename", "")).strip()
            if not filename or filename in {".", ".."} or "/" in filename or "\\" in filename or ":" in filename:
                fail(where, "use a filename without directories; set Plot directory separately.")
            if filename in filenames:
                fail(where, "duplicate filename would overwrite another plot.")
            filenames.add(filename)
    if node.type == "fit":
        obs_low, obs_high = bounds(p, "observable")
        if p.get("use_fit_range"):
            low, high = bounds(dict(minimum=p.get("fit_minimum"), maximum=p.get("fit_maximum")), "fit range")
            if None not in (low, high, obs_low, obs_high) and (low < obs_low or high > obs_high):
                fail("fit range", "fit range must lie inside the observable range.")
        if not str(p.get("expression", "")).strip():
            fail("observable", "expression is empty.")
        number(p, "bins", "plot", positive=True, integer=True)
        if str(p.get("strategy")) not in {"0", "1", "2"}:
            fail("fit", "strategy must be 0, 1 or 2.")
        parameters = rows("parameters")
        roles = MODEL_PARAMETERS.get(p.get("model"))
        if roles is None:
            fail("model", "choose a supported PDF.")
        elif len(parameters) != len(roles):
            fail("parameters", f"{p['model']} needs {len(roles)} parameters in order: {', '.join(roles)}. Use the model preset button.")
        names = set()
        for where, row in parameters:
            name = str(row.get("name", "")).strip()
            if not name or name in names:
                fail(where, "parameter name must be nonempty and unique.")
            names.add(name)
            value = number(row, "value", where)
            if not row.get("constant", False):
                low, high = bounds(row, where)
                if value is not None and low is not None and high is not None and not low <= value <= high:
                    fail(where, "initial value must lie within the parameter bounds.")
        outputs = [str(p[k]) for k in ("plot_filename", "result_filename", "workspace_filename") if p.get(k)]
        normalized = [str(PurePosixPath(path.replace("\\", "/"))) for path in outputs]
        if len(normalized) != len(set(normalized)):
            fail("outputs", "fit plot, result and workspace must use different filenames.")
    return errors
