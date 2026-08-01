from pathlib import Path
import shutil

import pytest

from analysis_studio.build import (
    compile_project,
    ensure_build_current,
    ensure_generation_current,
    generate_code,
)
from analysis_studio.model import Graph, Project
from analysis_studio.registry import NODE_SPECS
from analysis_studio.validation import validate_project


def add(graph, node_type, title=None, **properties):
    node = graph.add_node(NODE_SPECS[node_type], 0, 0, title)
    node.properties.update(properties)
    return node


def script_project(tmp_path: Path) -> tuple[Path, Project, Path]:
    code_dir = tmp_path / "code"
    code_dir.mkdir()
    source = code_dir / "hello.py"
    source.write_text("print('hello')\n", encoding="utf-8")
    project = Project.empty("portable")
    add(
        project.workflow,
        "custom_command",
        "Hello",
        code="code/hello.py",
        build_mode="auto",
    )
    project_path = tmp_path / "portable.astudio.json"
    project.save(project_path)
    return project_path, project, source


def test_generate_only_writes_generated_files_and_does_not_rewrite_project(tmp_path: Path):
    project_path, project, _source = script_project(tmp_path)
    before = project_path.read_bytes()
    written = generate_code(project, project_path, log=lambda _line: None)
    assert project_path.read_bytes() == before
    assert {path.relative_to(tmp_path).as_posix() for path in written} == {
        "generated/WORKFLOW.md"
    }
    assert not (tmp_path / "Untitled analysis.astudio.json").exists()
    ensure_generation_current(project_path)


def test_compile_copies_script_to_project_bin_and_source_change_invalidates_build(tmp_path: Path):
    project_path, project, source = script_project(tmp_path)
    generate_code(project, project_path, log=lambda _line: None)
    result = compile_project(project, project_path, log=lambda _line: None)
    executable = tmp_path / "bin" / "hello"
    assert result.outputs == [executable]
    assert executable.exists()
    assert executable.stat().st_mode & 0o111
    assert executable.read_text(encoding="utf-8").startswith("#!/usr/bin/env python3")
    ensure_build_current(project_path)

    source.write_text("print('changed')\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Build input file changed"):
        ensure_build_current(project_path)


def test_compile_standalone_cpp_without_root_or_framework(tmp_path: Path):
    if shutil.which("g++") is None:
        pytest.skip("g++ is not installed")
    code_dir = tmp_path / "code"
    code_dir.mkdir()
    source = code_dir / "hello.cc"
    source.write_text(
        "#include <iostream>\nint main(){std::cout << \"ok\"; return 0;}\n",
        encoding="utf-8",
    )
    project = Project.empty("cpp")
    add(
        project.workflow,
        "custom_command",
        "C++",
        code="code/hello.cc",
        build_mode="custom",
        compile_command='g++ "$AS_SOURCE" -o "$AS_OUTPUT"',
    )
    project_path = tmp_path / "cpp.astudio.json"
    project.save(project_path)
    generate_code(project, project_path, log=lambda _line: None)
    compile_project(project, project_path, log=lambda _line: None)
    assert (tmp_path / "bin" / "hello").exists()


def test_custom_code_must_be_project_relative(tmp_path: Path):
    project = Project.empty("invalid")
    add(
        project.workflow,
        "custom_command",
        "External",
        code="/outside/program.py",
    )
    errors = validate_project(project, tmp_path)
    assert any("relative to the project directory" in error for error in errors)


def test_for_each_token_is_rejected_in_build_time_code_path(tmp_path: Path):
    project = Project.empty("invalid-token")
    command = add(
        project.workflow,
        "custom_command",
        "Build once",
        code="code/{sample}.py",
        argv="{sample}",
    )
    region = project.workflow.add_region(
        0,
        0,
        {
            "source_mode": "values",
            "values": "a\nb",
            "tokens": [{"name": "sample", "source": "value"}],
        },
    )
    region.member_node_ids = [command.id]
    errors = validate_project(project, tmp_path)
    assert any("build-time property 'code'" in error for error in errors)


def test_custom_program_names_are_derived_and_numbered(tmp_path: Path):
    for directory in (tmp_path / "code" / "a", tmp_path / "code" / "b"):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "run.py").write_text("print('ok')\n", encoding="utf-8")
    project = Project.empty("names")
    first = add(project.workflow, "custom_command", "First", code="code/a/run.py")
    second = add(project.workflow, "custom_command", "Second", code="code/b/run.py")
    third = add(project.workflow, "custom_command", "Third", code="code/a/run.py")
    from analysis_studio.model import custom_command_output_names

    names = custom_command_output_names(project)
    assert names[first.id] == "run"
    assert names[second.id] == "run_2"
    assert names[third.id] == "run"
