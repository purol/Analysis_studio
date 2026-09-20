# Analysis Studio

Analysis Studio is a visual workflow editor for ROOT analyses built around
`Loader` / `Module` framework. It separates two levels that are
easy to mix together in shell scripts:

1. **Workflow level** — executables, file-wise fan-out, barriers, validation,
   fits, and scheduler dependencies.
2. **Loader level** — `Load`, `Cut`, `DefineNewVariable`, `BCS`, plots, and ROOT
   output modules within one executable.

## Installation

### GUI computer

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -e '.[gui]'
analysis-studio
```

### Headless server

The CLI has no Qt/PySide6 dependency:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
analysis-studio-cli --help
```

## Analysis task editor

Loader programs now start with a connected Loader Declaration → Samples → Cut Flow → End
template. Ordered tables collect input samples, selection stages and plot sets;
the Fit task handles common PDFs, parameter presets, fit ranges and ROOT outputs.
The class is fixed to Loader. Showing advanced settings only reveals the stored
C++ variable name. Task blocks live in Input, Selection, Transform, Plot, BDT,
Fit and Output; individual low-level calls are grouped under Advanced.

The [Belle_tau module guide](docs/belle_tau_modules.md) documents sample roles,
weights, ranked variables, candidate selection, FastBDT, histogram/dataset/profile
outputs and C++ support files. The audited 35 direct Loader methods all have GUI
adapters; this does not imply full migration of ROOT/RooStats algorithms.

Open `examples/analysis_tasks/tau_selection.astudio.json` for a working editor
example inspired by Belle_tau. See [the task editor guide](docs/analysis_tasks.md)
for usage, supported fit models and validation limits.

On Windows, launch from a GUI environment with:

```powershell
.\.venv\Scripts\python.exe run_analysis_studio.py
```

## License

Analysis Studio source is MIT licensed. PySide6/Qt is a separate optional GUI
dependency available under its own open-source or commercial terms. The CLI and
saved JSON format do not depend on Qt.
