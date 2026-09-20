"""Reproducible audit of direct Loader calls, not a C++ analysis importer."""
from collections import defaultdict
from pathlib import Path
import re


LOADER_COVERAGE = {
    "Load": "samples", "LoadWithCut": "samples", "Cut": "cut_flow",
    "PrintInformation": "print_information", "DrawTH1D": "plot_set", "DrawStack": "stack_plots",
    "DrawTH2D": "plots_2d", "PrintRootFile": "root_output", "PrintSeparateRootFile": "root_output",
    "BCS": "candidate_selection", "RandomBCS": "candidate_selection", "IsBCSValid": "candidate_selection",
    "RandomEventSelection": "event_split", "PrintEvent": "print_events", "AddWeight": "event_weight",
    "SetMC": "sample_roles", "SetData": "sample_roles", "SetSignal": "sample_roles", "SetBackground": "sample_roles",
    "DefineNewVariable": "variables", "RemoveVariable": "remove_variables",
    "ConditionalPairDefineNewVariable": "ranked_variables", "GetAverage": "variables", "GetStdDev": "variables",
    "GetDiff": "variables", "GetAdd": "variables", "GetRandom": "variables",
    "FastBDTTrain": "bdt_train", "FastBDTApplication": "bdt_apply",
    "DrawFOM": "bdt_evaluate", "DrawPunziFOM": "bdt_evaluate", "CalculateAUC": "bdt_evaluate",
    "FillTH1D": "histogram", "FillCustomizedTH1D": "histogram", "FillDataSet": "dataset",
    "FillTProfile": "profile", "end": "loader_end",
}


def audit_loader_calls(directory):
    uses = defaultdict(set)
    files = sorted(Path(directory).glob("*.cc"))
    token = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*.*?\*/', re.S)
    for path in files:
        source = token.sub(lambda m: " " if m.group().startswith(("//", "/*")) else m.group(), path.read_text(encoding="utf-8", errors="replace"))
        names = re.findall(r"\bLoader\s+(\w+)\s*\(", source)
        for name in names:
            for method in re.findall(r"\b" + re.escape(name) + r"\s*\.\s*(\w+)\s*\(", source):
                uses[method].add(path.name)
    return {
        "files_scanned": len(files),
        "scope": "Direct Loader declarations and dot-method calls in *.cc; excludes comments. Does not analyze ROOT/RooStats algorithms, aliases or macros.",
        "methods": [{"method": method, "module": LOADER_COVERAGE.get(method), "files": sorted(paths)} for method, paths in sorted(uses.items())],
    }
