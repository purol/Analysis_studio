from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import os
import shlex
import shutil
import stat
import subprocess
from typing import Callable, Iterable

from .codegen import generate_loader_cpp, generate_workflow_summary
from .model import (
    Project,
    WorkflowNode,
    custom_command_output_name,
    custom_command_output_names,
    loader_source_filename,
    safe_program_name,
)
from .validation import validate_project


LogCallback = Callable[[str], None]
GENERATED_DIR = "generated"
BIN_DIR = "bin"
STATE_DIR = ".analysis-studio"
GENERATION_MANIFEST = "generation_manifest.json"
BUILD_MANIFEST = "build_manifest.json"


def project_root(project_path: str | Path) -> Path:
    path = Path(project_path).resolve()
    return path.parent if path.is_file() or path.suffix else path


def _json_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _file_digests(root: Path, paths: Iterable[Path]) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in paths:
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(root.resolve())
        except ValueError:
            # Build inputs are normally project-local. Framework files are not
            # included because a submodule revision may be managed separately.
            continue
        if resolved.is_file():
            result[str(relative)] = hashlib.sha256(resolved.read_bytes()).hexdigest()
    return result


def _verify_digests(root: Path, values: object, label: str) -> None:
    if not isinstance(values, dict):
        raise RuntimeError(f"{label} manifest is incomplete. Run the step again.")
    for relative, expected in values.items():
        path = root / str(relative)
        if not path.is_file():
            raise RuntimeError(f"{label} file is missing: {relative}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != str(expected):
            raise RuntimeError(f"{label} file changed since the step ran: {relative}")


def _write_manifest(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _read_manifest(path: Path) -> dict[str, object] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def generation_manifest_path(root: Path) -> Path:
    return root / STATE_DIR / GENERATION_MANIFEST


def build_manifest_path(root: Path) -> Path:
    return root / STATE_DIR / BUILD_MANIFEST


def generate_code(
    project: Project,
    project_path: str | Path,
    log: LogCallback = print,
) -> list[Path]:
    project_file = Path(project_path).resolve()
    if not project_file.exists():
        raise FileNotFoundError("Save the project JSON before generating code.")
    root = project_file.parent
    errors = validate_project(project, root)
    if errors:
        raise ValueError("Project validation failed:\n" + "\n".join(errors))

    output = root / GENERATED_DIR
    output.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for graph in project.loader_programs.values():
        if not graph.nodes:
            continue
        path = output / loader_source_filename(graph)
        path.write_text(generate_loader_cpp(graph), encoding="utf-8")
        written.append(path)
        log(f"Generated {path.relative_to(root)}")

    summary = output / "WORKFLOW.md"
    summary.write_text(generate_workflow_summary(project), encoding="utf-8")
    written.append(summary)
    log(f"Generated {summary.relative_to(root)}")

    _write_manifest(
        generation_manifest_path(root),
        {
            "project": project_file.name,
            "project_sha256": _json_digest(project_file),
            "outputs": [str(path.relative_to(root)) for path in written],
            "output_sha256": _file_digests(root, written),
        },
    )
    return written


def ensure_generation_current(project_path: str | Path) -> None:
    project_file = Path(project_path).resolve()
    root = project_file.parent
    manifest = _read_manifest(generation_manifest_path(root))
    if not manifest or manifest.get("project_sha256") != _json_digest(project_file):
        raise RuntimeError(
            "Generated code is missing or older than the saved project. Run Generate Code first."
        )
    _verify_digests(root, manifest.get("output_sha256"), "Generated")


def ensure_build_current(project_path: str | Path) -> None:
    project_file = Path(project_path).resolve()
    root = project_file.parent
    manifest = _read_manifest(build_manifest_path(root))
    if not manifest or manifest.get("project_sha256") != _json_digest(project_file):
        raise RuntimeError(
            "Compiled programs are missing or older than the saved project. Run Compile first."
        )
    for relative in manifest.get("outputs", []):
        if not (root / str(relative)).exists():
            raise RuntimeError(f"Compiled artifact is missing: {relative}")
    _verify_digests(root, manifest.get("input_sha256"), "Build input")


def _lines(value: object) -> list[str]:
    return [line.strip() for line in str(value or "").splitlines() if line.strip()]


def _resolve_project_file(root: Path, value: object, label: str) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"{label} is empty.")
    path = Path(raw)
    if path.is_absolute():
        raise ValueError(
            f"{label} must be project-relative for portability, not absolute: {path}"
        )
    resolved = (root / path).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} escapes the project directory: {path}") from exc
    return resolved


def _resolve_framework(root: Path, project: Project) -> Path:
    configured = Path(str(project.build_options.get("belle2_analysis_dir", "Belle2_analysis")))
    candidates = []
    if configured.is_absolute():
        candidates.append(configured)
    else:
        candidates.extend(
            [
                root / configured,
                Path(__file__).resolve().parents[2] / configured,
            ]
        )
    for candidate in candidates:
        if (candidate / "include" / "Loader.h").exists():
            return candidate.resolve()
    rendered = ", ".join(str(path) for path in candidates)
    raise FileNotFoundError(
        "Belle2_analysis was not found. Configure its directory in Build Settings. "
        f"Checked: {rendered}"
    )


def _root_config(*args: str) -> list[str]:
    completed = subprocess.run(
        ["root-config", *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return shlex.split(completed.stdout.strip())


def _run(command: list[str], cwd: Path, log: LogCallback) -> None:
    log("$ " + shlex.join(command))
    completed = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if completed.stdout:
        for line in completed.stdout.splitlines():
            log(line)
    if completed.returncode != 0:
        raise RuntimeError(
            f"Build command failed with exit code {completed.returncode}: {shlex.join(command)}"
        )


def _compiler_prefix(project: Project) -> list[str]:
    compiler = str(project.build_options.get("compiler", "g++")).strip() or "g++"
    standard = str(project.build_options.get("cpp_standard", "c++17")).strip() or "c++17"
    return [compiler, f"-std={standard}"]


def _framework_flags(framework: Path) -> tuple[list[str], list[str]]:
    compile_flags = [
        f"-I{framework / 'include'}",
        f"-I{framework / 'FastBDT' / 'include'}",
    ]
    link_flags = [f"-L{framework / 'lib'}"]
    return compile_flags, link_flags


def _compile_cpp(
    project: Project,
    root: Path,
    source: Path,
    output: Path,
    additional_sources: Iterable[Path],
    extra_compile: Iterable[str],
    extra_link: Iterable[str],
    framework: Path | None,
    log: LogCallback,
    use_analysis_framework: bool = True,
) -> None:
    framework_compile: list[str] = []
    framework_link: list[str] = []
    root_compile: list[str] = []
    root_link: list[str] = []
    loader_libraries: list[str] = []
    if use_analysis_framework:
        if framework is None:
            raise ValueError("Belle2_analysis is required for this C++ build.")
        framework_compile, framework_link = _framework_flags(framework)
        root_compile = _root_config("--cflags")
        root_link = _root_config("--ldflags", "--glibs")
        loader_libraries = _lines(project.build_options.get("loader_libraries", ""))
    command = [
        *_compiler_prefix(project),
        *root_compile,
        *framework_compile,
        *_lines(project.build_options.get("common_compile_flags", "")),
        *extra_compile,
        str(source),
        *(str(item) for item in additional_sources),
        "-o",
        str(output),
        *root_link,
        *framework_link,
        *loader_libraries,
        *_lines(project.build_options.get("common_link_flags", "")),
        *extra_link,
    ]
    _run(command, root, log)


def _copy_script_or_binary(source: Path, output: Path) -> None:
    data = source.read_bytes()
    if source.suffix.lower() == ".py" and not data.startswith(b"#!"):
        data = b"#!/usr/bin/env python3\n" + data
    elif source.suffix.lower() in {".sh", ".bash"} and not data.startswith(b"#!"):
        data = b"#!/usr/bin/env bash\n" + data
    output.write_bytes(data)
    output.chmod(output.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@dataclass
class BuildResult:
    outputs: list[Path]


def compile_project(
    project: Project,
    project_path: str | Path,
    log: LogCallback = print,
) -> BuildResult:
    project_file = Path(project_path).resolve()
    root = project_file.parent
    ensure_generation_current(project_file)
    errors = validate_project(project, root)
    if errors:
        raise ValueError("Project validation failed:\n" + "\n".join(errors))

    generated = root / GENERATED_DIR
    binary_dir = root / BIN_DIR
    binary_dir.mkdir(parents=True, exist_ok=True)
    framework: Path | None = None

    def require_framework() -> Path:
        nonlocal framework
        if framework is None:
            framework = _resolve_framework(root, project)
        return framework

    outputs: list[Path] = []
    build_inputs: list[Path] = []

    for graph in project.loader_programs.values():
        if not graph.nodes:
            continue
        source = generated / loader_source_filename(graph)
        build_inputs.append(source)
        output = binary_dir / safe_program_name(graph.name)
        _compile_cpp(project, root, source, output, [], [], [], require_framework(), log)
        outputs.append(output)
        log(f"Built {output.relative_to(root)}")

    output_names = custom_command_output_names(project)
    seen_custom: set[tuple[str, str]] = set()
    for node in project.workflow.nodes:
        if node.type != "custom_command":
            continue
        key = (str(node.properties.get("code", "")), custom_command_output_name(node, project))
        if key in seen_custom:
            continue
        seen_custom.add(key)
        source = _resolve_project_file(root, node.properties.get("code", ""), f"{node.title}: Code")
        build_inputs.append(source)
        if not source.exists():
            raise FileNotFoundError(f"{node.title}: code file does not exist: {source}")
        output = binary_dir / output_names[node.id]
        mode = str(node.properties.get("build_mode", "auto"))
        suffix = source.suffix.lower()

        if mode == "custom":
            command = str(node.properties.get("compile_command", "")).strip()
            if not command:
                raise ValueError(f"{node.title}: custom build mode needs a compile command.")
            additional = [
                _resolve_project_file(root, value, f"{node.title}: additional source")
                for value in _lines(node.properties.get("additional_sources", ""))
            ]
            build_inputs.extend(additional)
            environment = os.environ.copy()
            environment.update(
                {
                    "AS_SOURCE": str(source),
                    "AS_OUTPUT": str(output),
                    "AS_PROJECT_DIR": str(root),
                }
            )
            compile_flags = _lines(node.properties.get("compile_flags", ""))
            link_flags = _lines(node.properties.get("link_flags", ""))
            shell_script = "\n".join(
                [
                    "AS_ADDITIONAL_SOURCES=("
                    + shlex.join(str(item) for item in additional)
                    + ")",
                    "AS_COMPILE_FLAGS=(" + shlex.join(compile_flags) + ")",
                    "AS_LINK_FLAGS=(" + shlex.join(link_flags) + ")",
                    command,
                ]
            )
            log("$ " + command)
            completed = subprocess.run(
                ["/bin/bash", "-lc", shell_script],
                cwd=root,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            if completed.stdout:
                for line in completed.stdout.splitlines():
                    log(line)
            if completed.returncode != 0:
                raise RuntimeError(
                    f"{node.title}: custom build command failed with exit code "
                    f"{completed.returncode}."
                )
        elif suffix not in {".cc", ".cpp", ".cxx", ".c++"}:
            _copy_script_or_binary(source, output)
        else:
            _compile_cpp(
                project,
                root,
                source,
                output,
                [],
                [],
                [],
                require_framework(),
                log,
                use_analysis_framework=True,
            )
        if not output.exists():
            raise RuntimeError(f"{node.title}: build did not create {output}")
        output.chmod(output.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        outputs.append(output)
        log(f"Built {output.relative_to(root)}")

    _write_manifest(
        build_manifest_path(root),
        {
            "project": project_file.name,
            "project_sha256": _json_digest(project_file),
            "outputs": [str(path.relative_to(root)) for path in outputs],
            "input_sha256": _file_digests(root, build_inputs),
        },
    )
    return BuildResult(outputs)
