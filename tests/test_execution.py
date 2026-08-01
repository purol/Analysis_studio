from pathlib import Path
import subprocess

import pytest

from analysis_studio.execution import HTCondorExporter, LSFExecutor, LocalExecutor
from analysis_studio.model import Graph, Project
from analysis_studio.registry import NODE_SPECS


def add(graph, node_type, title=None, **properties):
    node = graph.add_node(NODE_SPECS[node_type], 0, 0, title)
    node.properties.update(properties)
    return node


def connect(graph, first, second):
    graph.add_edge(first.id, "out", second.id, "in")


def make_project_program(tmp_path: Path, name: str = "my_command") -> tuple[str, str]:
    code_dir = tmp_path / "code"
    code_dir.mkdir(exist_ok=True)
    source = code_dir / f"{name}.sh"
    source.write_text("#!/usr/bin/env bash\nexec /bin/sh \"$@\"\n", encoding="utf-8")
    source.chmod(0o755)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    executable = bin_dir / name
    executable.write_bytes(source.read_bytes())
    executable.chmod(0o755)
    return f"code/{name}.sh", name


def test_local_executor_runs_dependency_graph(tmp_path: Path):
    code, output_name = make_project_program(tmp_path)
    project = Project(
        name="local",
        workflow=Graph("workflow", "Workflow", "workflow"),
        loader_programs={},
        backend_options={"local_workers": 2, "condor_universe": "vanilla"},
    )
    output = tmp_path / "result.txt"
    first = add(
        project.workflow,
        "custom_command",
        "First",
        code=code,
        argv=f"-c\nprintf first > {output}",
    )
    second = add(
        project.workflow,
        "custom_command",
        "Second",
        code=code,
        argv=f"-c\nprintf second >> {output}",
    )
    connect(project.workflow, first, second)
    LocalExecutor(lambda _: None).run(project, tmp_path)
    assert output.read_text(encoding="utf-8") == "firstsecond"


def test_condor_export_preserves_wait_dependencies(tmp_path: Path):
    code, output_name = make_project_program(tmp_path, "echo_command")
    project = Project(
        name="condor",
        workflow=Graph("workflow", "Workflow", "workflow"),
        loader_programs={},
    )
    add(project.workflow, "custom_command", "A", code=code, argv="A")
    add(project.workflow, "custom_command", "B", code=code, argv="B")
    wait = add(project.workflow, "wait", "Wait AB", wait_for="A\nB")
    c = add(project.workflow, "custom_command", "C", code=code, argv="C")
    connect(project.workflow, wait, c)

    dag = HTCondorExporter(lambda _: None).export(project, tmp_path / "dag", tmp_path)
    text = dag.read_text(encoding="utf-8")
    assert text.count("JOB ") == 3
    parent_lines = [line for line in text.splitlines() if line.startswith("PARENT")]
    assert len(parent_lines) == 1
    assert parent_lines[0].count("task_") == 3


def _lsf_project(tmp_path: Path) -> Project:
    code, output_name = make_project_program(tmp_path, "lsf_command")
    project = Project(
        name="lsf",
        workflow=Graph("workflow", "Workflow", "workflow"),
        loader_programs={},
        backend_options={
            "local_workers": 2,
            "lsf_poll_seconds": 1,
            "lsf_max_active_jobs": 10,
            "lsf_cancel_on_failure": True,
            "condor_universe": "vanilla",
        },
    )
    first = add(
        project.workflow,
        "custom_command",
        "First",
        code=code,
        argv="first",
        lsf_queue="s",
    )
    second = add(
        project.workflow,
        "custom_command",
        "Second",
        code=code,
        argv="second",
        lsf_queue="l",
    )
    connect(project.workflow, first, second)
    return project


def test_lsf_monitors_success_and_never_uses_bsub_wait(tmp_path: Path, monkeypatch):
    project = _lsf_project(tmp_path)
    calls: list[list[str]] = []
    next_job = iter(["101", "102"])
    status_calls = 0

    def fake_run(command, **kwargs):
        nonlocal status_calls
        calls.append(list(command))
        if command[0] == "bsub":
            job = next(next_job)
            return subprocess.CompletedProcess(command, 0, f"Job <{job}> is submitted\n", "")
        if command[0] == "bjobs":
            status_calls += 1
            ids = [part for part in command if part.isdigit()]
            stdout = "".join(f"{job} DONE 0\n" for job in ids)
            return subprocess.CompletedProcess(command, 0, stdout, "")
        if command[0] == "bkill":
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(command)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("analysis_studio.execution.time.sleep", lambda _seconds: None)
    LSFExecutor(log=lambda _: None).run(project, tmp_path)

    bsubs = [call for call in calls if call[0] == "bsub"]
    assert len(bsubs) == 2
    assert all("-w" not in call for call in bsubs)
    assert bsubs[0][bsubs[0].index("-q") + 1] == "s"
    assert bsubs[1][bsubs[1].index("-q") + 1] == "l"
    assert status_calls >= 2


def test_lsf_failure_prevents_downstream_submission(tmp_path: Path, monkeypatch):
    project = _lsf_project(tmp_path)
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(list(command))
        if command[0] == "bsub":
            return subprocess.CompletedProcess(command, 0, "Job <201> is submitted\n", "")
        if command[0] == "bjobs":
            return subprocess.CompletedProcess(command, 0, "201 EXIT 17\n", "")
        if command[0] == "bkill":
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(command)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("analysis_studio.execution.time.sleep", lambda _seconds: None)
    with pytest.raises(RuntimeError, match="Downstream jobs were not submitted"):
        LSFExecutor(log=lambda _: None).run(project, tmp_path)
    assert len([call for call in calls if call[0] == "bsub"]) == 1


def test_lsf_global_active_job_limit_applies_across_blocks(tmp_path: Path, monkeypatch):
    code, _name = make_project_program(tmp_path, "limited")
    project = Project(
        name="limited",
        workflow=Graph("workflow", "Workflow", "workflow"),
        loader_programs={},
        backend_options={
            "local_workers": 2,
            "lsf_poll_seconds": 1,
            "lsf_max_active_jobs": 1,
            "lsf_cancel_on_failure": True,
            "condor_universe": "vanilla",
        },
    )
    add(project.workflow, "custom_command", "A", code=code, lsf_queue="s")
    add(project.workflow, "custom_command", "B", code=code, lsf_queue="h")

    calls: list[list[str]] = []
    job_ids = iter(["301", "302"])

    def fake_run(command, **kwargs):
        calls.append(list(command))
        if command[0] == "bsub":
            job = next(job_ids)
            return subprocess.CompletedProcess(command, 0, f"Job <{job}> is submitted\n", "")
        if command[0] == "bjobs":
            ids = [part for part in command if part.isdigit()]
            return subprocess.CompletedProcess(
                command, 0, "".join(f"{job} DONE 0\n" for job in ids), ""
            )
        raise AssertionError(command)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("analysis_studio.execution.time.sleep", lambda _seconds: None)
    LSFExecutor(log=lambda _: None).run(project, tmp_path)

    kinds = [call[0] for call in calls]
    assert kinds[:3] == ["bsub", "bjobs", "bsub"]


def test_local_executor_saves_separate_block_logs_and_creates_directories(tmp_path: Path):
    code_dir = tmp_path / "code"
    code_dir.mkdir(exist_ok=True)
    source = code_dir / "emit.sh"
    source.write_text(
        "#!/usr/bin/env bash\nprintf 'hello stdout\\n'\nprintf 'hello stderr\\n' >&2\n",
        encoding="utf-8",
    )
    source.chmod(0o755)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    executable = bin_dir / "emit"
    executable.write_bytes(source.read_bytes())
    executable.chmod(0o755)

    project = Project(
        name="logs",
        workflow=Graph("workflow", "Workflow", "workflow"),
        loader_programs={},
        backend_options={"local_workers": 1},
    )
    node = add(
        project.workflow,
        "custom_command",
        "Emit output",
        code="code/emit.sh",
        mkdir_p="results/nested",
        log_prefix="stdout_",
        log_suffix="_done",
        err_prefix="stderr_",
        err_suffix="_failed",
    )
    LocalExecutor(lambda _: None).run(project, tmp_path)

    assert (tmp_path / "results" / "nested").is_dir()
    block_dirs = list((tmp_path / "logs" / "local").glob("Emit_output__*"))
    assert len(block_dirs) == 1
    log_files = list(block_dirs[0].glob("stdout_*_done.log"))
    err_files = list(block_dirs[0].glob("stderr_*_failed.err"))
    assert len(log_files) == 1
    assert len(err_files) == 1
    assert log_files[0].read_text(encoding="utf-8") == "hello stdout\n"
    assert err_files[0].read_text(encoding="utf-8") == "hello stderr\n"


def test_lsf_uses_block_name_and_block_log_directories(tmp_path: Path, monkeypatch):
    code, _name = make_project_program(tmp_path, "lsf_logs")
    project = Project(
        name="lsf-logs",
        workflow=Graph("workflow", "Workflow", "workflow"),
        loader_programs={},
        backend_options={
            "lsf_poll_seconds": 1,
            "lsf_max_active_jobs": 1,
            "lsf_cancel_on_failure": True,
        },
    )
    add(
        project.workflow,
        "custom_command",
        "Repeated Job Name",
        code=code,
        lsf_queue="s",
        mkdir_p="results",
        log_prefix="log_",
        log_suffix="_x",
        err_prefix="err_",
        err_suffix="_y",
    )
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(list(command))
        if command[0] == "bsub":
            return subprocess.CompletedProcess(command, 0, "Job <901> is submitted\n", "")
        if command[0] == "bjobs":
            return subprocess.CompletedProcess(command, 0, "901 DONE 0\n", "")
        raise AssertionError(command)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("analysis_studio.execution.time.sleep", lambda _seconds: None)
    LSFExecutor(log=lambda _: None).run(project, tmp_path)

    bsub = next(call for call in calls if call[0] == "bsub")
    assert bsub[bsub.index("-J") + 1] == "Repeated Job Name"
    stdout = Path(bsub[bsub.index("-o") + 1])
    stderr = Path(bsub[bsub.index("-e") + 1])
    assert stdout.parent.parent.name == "lsf"
    assert stdout.parent.name.startswith("Repeated_Job_Name__")
    assert stdout.name.startswith("log_") and stdout.name.endswith("_x.log")
    assert stderr.name.startswith("err_") and stderr.name.endswith("_y.err")
    assert "%J" in stdout.name and "%J" in stderr.name
    assert (tmp_path / "results").is_dir()
