from __future__ import annotations

from .model import NodeSpec, PropertySpec


P = PropertySpec

ARGV_HELP = (
    "One argument per line. For Each variables may be used only while this "
    "block is inside a For Each region. The surrounding region lists every "
    "available variable and its preview value."
)

EXECUTION_BACKEND_PROPERTIES = (
    P(
        "local_max_parallel",
        "Local maximum parallel commands",
        "int",
        0,
        help="0 uses the project-wide Local worker count.",
    ),
    P(
        "lsf_queue",
        "LSF queue override",
        "text",
        "",
        help="Leave empty to use the project-wide default queue.",
    ),
    P(
        "lsf_max_inflight",
        "LSF maximum active jobs",
        "int",
        0,
        help=(
            "Maximum submitted/running instances of this block. 0 uses the "
            "project-wide LSF limit. Useful when a For Each expands to many jobs."
        ),
    ),
    P(
        "lsf_extra_options",
        "LSF extra options",
        "argv",
        "",
        multiline=True,
        help=(
            "One bsub option or value per line, for example: -R on one line and "
            "rusage[mem=4000] on the next. Do not include -q, -J, -o, -e or -w."
        ),
    ),
)

FOREACH_COMMON_PROPERTIES = (
    P(
        "source_mode",
        "Source",
        "choice",
        "root_files",
        ("root_files", "csv_rows", "values"),
    ),
)

FOREACH_SOURCE_PROPERTIES = {
    "root_files": (
        P("directory", "ROOT directory", "path", "./Ntuple"),
        P("pattern", "ROOT pattern", "text", "*.root"),
    ),
    "csv_rows": (
        P("csv_file", "CSV file", "path", "./items.csv"),
        P("delimiter", "CSV delimiter", "text", ","),
        P("has_header", "CSV has header", "bool", True),
    ),
    "values": (
        P(
            "values",
            "Values",
            "text",
            "value_a\nvalue_b",
            multiline=True,
            help="One value per line.",
        ),
    ),
}

FOREACH_TRAILING_PROPERTIES = (
    P(
        "max_parallel",
        "Maximum parallel commands (Local)",
        "int",
        0,
        help=(
            "0 uses the backend-wide worker setting. Nested limits are applied "
            "together. LSF uses each workflow block's LSF active-job limit."
        ),
    ),
)

FOREACH_PROPERTIES = (
    *FOREACH_COMMON_PROPERTIES,
    *FOREACH_SOURCE_PROPERTIES["root_files"],
    *FOREACH_SOURCE_PROPERTIES["csv_rows"],
    *FOREACH_SOURCE_PROPERTIES["values"],
    *FOREACH_TRAILING_PROPERTIES,
)
FOREACH_DEFAULTS = {item.name: item.default for item in FOREACH_PROPERTIES}
FOREACH_DEFAULTS["tokens"] = []


def foreach_properties_for_mode(mode: str) -> tuple[PropertySpec, ...]:
    return (
        *FOREACH_COMMON_PROPERTIES,
        *FOREACH_SOURCE_PROPERTIES.get(mode, ()),
        *FOREACH_TRAILING_PROPERTIES,
    )


def _specs() -> list[NodeSpec]:
    return [
        NodeSpec(
            "loader_execute",
            "Loader Execute",
            "Execution",
            "workflow",
            "#7651a8",
            properties=(
                P(
                    "loader_program",
                    "Loader program",
                    "loader_program_ref",
                    "",
                    help="Generated and compiled Loader program represented by a Loader tab.",
                ),
                P("argv", "argv", "argv", "", multiline=True, help=ARGV_HELP),
                P("working_directory", "Working directory", "path", ""),
                P("output_dir", "Output directory", "path", ""),
                P("job_name", "Job name", "text", "Loader"),
                *EXECUTION_BACKEND_PROPERTIES,
            ),
        ),
        NodeSpec(
            "custom_command",
            "Custom Command",
            "Execution",
            "workflow",
            "#356b59",
            properties=(
                P(
                    "code",
                    "Code / script",
                    "path",
                    "code/my_command.py",
                    help=(
                        "Project-relative source path. Keep it inside the directory "
                        "containing the .astudio.json file so the project is portable."
                    ),
                ),
                P(
                    "output_name",
                    "Program name",
                    "text",
                    "my_command",
                    help="Executable/script name created inside the project's bin directory.",
                ),
                P(
                    "build_mode",
                    "Build mode",
                    "choice",
                    "auto",
                    ("auto", "custom", "copy"),
                    help=(
                        "auto compiles C/C++ and copies Python/shell files; custom runs "
                        "the command below; copy always copies the file into bin."
                    ),
                ),
                P(
                    "use_analysis_framework",
                    "Use ROOT / Belle2_analysis",
                    "bool",
                    True,
                    help=(
                        "For automatic C++ builds, add root-config flags and the "
                        "Belle2_analysis/FastBDT include and library settings. Disable "
                        "this for an ordinary standalone C++ program."
                    ),
                ),
                P(
                    "compile_command",
                    "Custom compile command",
                    "text",
                    "",
                    multiline=True,
                    help=(
                        "Used only in custom mode. The shell environment provides "
                        "AS_SOURCE, AS_OUTPUT, AS_PROJECT_DIR and AS_BELLE2_ANALYSIS."
                    ),
                ),
                P(
                    "additional_sources",
                    "Additional C/C++ sources",
                    "text",
                    "",
                    multiline=True,
                    help="One project-relative source file per line.",
                ),
                P(
                    "compile_flags",
                    "Additional compile flags",
                    "argv",
                    "",
                    multiline=True,
                    help="One flag or value per line.",
                ),
                P(
                    "link_flags",
                    "Additional link flags",
                    "argv",
                    "",
                    multiline=True,
                    help="One flag or value per line.",
                ),
                P("argv", "argv", "argv", "", multiline=True, help=ARGV_HELP),
                P("working_directory", "Working directory", "path", ""),
                P("output_dir", "Output directory", "path", ""),
                P("job_name", "Job name", "text", "Command"),
                *EXECUTION_BACKEND_PROPERTIES,
            ),
        ),
        NodeSpec(
            "wait",
            "Wait",
            "Control",
            "workflow",
            "#666b73",
            properties=(
                P(
                    "wait_for",
                    "Wait for block names",
                    "node_refs",
                    "",
                    multiline=True,
                    help=(
                        "One block name or node ID per line. Incoming connections are "
                        "also dependencies. With LSF, downstream jobs are submitted only "
                        "after all dependencies have actually finished successfully."
                    ),
                ),
            ),
        ),
        NodeSpec(
            "loader_decl",
            "Loader Declaration",
            "Loader",
            "loader",
            "#7651a8",
            inputs=(),
            outputs=("out",),
            properties=(
                P("variable_name", "C++ variable name", "text", "loader"),
                P("branch", "Tree / branch name", "text", "tau_lfv"),
                P("loader_class", "Loader class", "text", "Loader"),
            ),
        ),
        NodeSpec(
            "loader_end",
            "End",
            "Loader",
            "loader",
            "#7651a8",
            inputs=("in",),
            outputs=(),
            properties=(),
        ),
        NodeSpec(
            "load",
            "Load",
            "Input",
            "loader",
            "#4263a8",
            properties=(
                P("directory_cpp", "Directory (C++)", "text", "argv[1]"),
                P("including_cpp", "Including string (C++)", "text", "argv[2]"),
                P("label", "Label", "text", "SIGNAL"),
            ),
        ),
        NodeSpec(
            "load_with_cut",
            "Load With Cut",
            "Input",
            "loader",
            "#4263a8",
            properties=(
                P("directory_cpp", "Directory (C++)", "text", "argv[1]"),
                P("including_cpp", "Including string (C++)", "text", "argv[2]"),
                P("label", "Label", "text", "SIGNAL"),
                P("condition", "Condition", "text", "1", multiline=True),
            ),
        ),
        NodeSpec(
            "cut",
            "Cut",
            "Selection",
            "loader",
            "#a06c2b",
            properties=(P("condition", "Condition", "text", "M > 1.5", multiline=True),),
        ),
        NodeSpec(
            "draw_th1d",
            "Draw TH1D",
            "Plot",
            "loader",
            "#9a4d64",
            properties=(
                P("expression", "Expression", "text", "M"),
                P("title", "ROOT title", "text", ";M [GeV];Events"),
                P("bins", "Bins", "int", 50),
                P("minimum", "Minimum", "float", 1.5),
                P("maximum", "Maximum", "float", 1.9),
                P("filename", "PNG filename", "text", "M.png"),
            ),
        ),
        NodeSpec(
            "print_root",
            "Print ROOT File",
            "Output",
            "loader",
            "#9a4d64",
            properties=(P("filename", "ROOT filename", "text", "./selected.root"),),
        ),
        NodeSpec(
            "bcs",
            "Best Candidate Selection",
            "Selection",
            "loader",
            "#a06c2b",
            properties=(
                P("expression", "Expression", "text", "chiProb"),
                P("criteria", "Criteria", "choice", "highest", ("highest", "lowest")),
            ),
        ),
        NodeSpec(
            "define_variable",
            "Define New Variable",
            "Transform",
            "loader",
            "#3d8060",
            properties=(
                P("equation", "Equation", "text", "x + y", multiline=True),
                P("name", "New variable", "text", "new_variable"),
            ),
        ),
        NodeSpec(
            "raw_cpp",
            "Custom C++",
            "Advanced",
            "loader",
            "#814f87",
            properties=(
                P(
                    "code",
                    "C++ code",
                    "text",
                    "// Custom C++ statement(s)",
                    multiline=True,
                    help=(
                        "Inserted verbatim at this exact position. Use it for "
                        "declarations or Loader functions not represented yet."
                    ),
                ),
            ),
        ),
    ]


NODE_SPECS = {spec.key: spec for spec in _specs()}


def specs_for_scope(scope: str) -> list[NodeSpec]:
    return [spec for spec in NODE_SPECS.values() if spec.scope == scope]
