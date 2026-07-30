# Analysis Studio

Analysis Studio is a visual workflow editor for ROOT analyses built around
`Loader` / `Module` framework. It separates two levels that are
easy to mix together in shell scripts:

1. **Workflow level** — executables, file-wise fan-out, barriers, validation,
   fits, and scheduler dependencies.
2. **Loader level** — `Load`, `Cut`, `DefineNewVariable`, `BCS`, plots, and ROOT
   output modules within one executable.

## Installation

Use Python 3.10 or newer:

```bash
cd analysis_studio
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -e .
analysis
```

On a machine with no display (for example, a login node), start the GUI through
X11 forwarding, a remote desktop, or run it on your local computer. The
generated JSON and C++ files remain portable.

## License

Analysis Studio source code is released under the MIT License. PySide6 and Qt
remain under their respective licenses.
