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

## License

Analysis Studio source is MIT licensed. PySide6/Qt is a separate optional GUI
dependency available under its own open-source or commercial terms. The CLI and
saved JSON format do not depend on Qt.
