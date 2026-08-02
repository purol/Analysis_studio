from __future__ import annotations

from collections import Counter
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
import re
import shlex
import subprocess
import time
from typing import Callable, Iterable

from .model import Project
from .planning import ExecutionPlan, PlanTask, build_execution_plan


LogCallback = Callable[[str], None]


def _project_directory(project_path: str | Path | None) -> Path:
    if project_path is None:
        return Path.cwd()
    path = Path(project_path)
    return path.parent if path.suffix else path


def _safe_directory_component(value: str, fallback: str) -> str:
    rendered = re.sub(r"[^\w.-]+", "_", str(value), flags=re.UNICODE).strip("._")
    return rendered or fallback


def _safe_filename_fragment(value: str) -> str:
    return re.sub(r"[^\w.-]+", "_", str(value or ""), flags=re.UNICODE)


def _task_log_paths(
    project_root: Path,
    backend: str,
    task: PlanTask,
    index: int,
    job_token: str,
) -> tuple[Path, Path]:
    block = _safe_directory_component(task.block_name, "block")
    node_suffix = task.node_id.rsplit("_", 1)[-1][:8]
    directory = project_root / "logs" / backend / f"{block}__{node_suffix}"
    directory.mkdir(parents=True, exist_ok=True)
    task_suffix = task.id.rsplit("_", 1)[-1][:8]
    base = f"{index:05d}_{job_token}_{task_suffix}"
    stdout = directory / (
        f"{_safe_filename_fragment(task.log_err_prefix)}{base}"
        f"{_safe_filename_fragment(task.log_err_suffix)}.log"
    )
    stderr = directory / (
        f"{_safe_filename_fragment(task.log_err_prefix)}{base}"
        f"{_safe_filename_fragment(task.log_err_suffix)}.err"
    )
    return stdout, stderr


def _tail(path: Path, max_characters: int = 8000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-max_characters:]


class LocalExecutor:
    def __init__(self, log: LogCallback = print) -> None:
        self.log = log

    def run(
        self,
        project: Project,
        project_path: str | Path | None = None,
    ) -> ExecutionPlan:
        root = _project_directory(project_path)
        plan = build_execution_plan(project, root)
        workers = max(1, int(project.backend_options.get("local_workers", 1)))
        self.log(f"Expanded workflow: {len(plan.tasks)} command task(s)")
        if not plan.tasks:
            return plan

        ordered = plan.topological_order()
        sequence = {task.id: index for index, task in enumerate(ordered)}
        pending = {task.id: task for task in plan.tasks}
        completed: set[str] = set()
        running: dict[Future[tuple[str, Path, Path]], PlanTask] = {}

        def allowed(task: PlanTask) -> bool:
            return set(task.dependencies).issubset(completed)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            while pending or running:
                made_progress = True
                while made_progress and len(running) < workers:
                    made_progress = False
                    for task_id, task in list(pending.items()):
                        if len(running) >= workers:
                            break
                        if not allowed(task):
                            continue
                        running[
                            pool.submit(self._run_task, task, sequence[task.id], root)
                        ] = task
                        del pending[task_id]
                        made_progress = True

                if not running:
                    blocked = ", ".join(task.title for task in pending.values())
                    raise RuntimeError(
                        "No runnable task remains. The execution plan is blocked: " + blocked
                    )

                finished, _ = wait(running, return_when=FIRST_COMPLETED)
                for future in finished:
                    task = running.pop(future)
                    try:
                        title, stdout_path, stderr_path = future.result()
                    except Exception:
                        for other in running:
                            other.cancel()
                        raise
                    for line in stdout_path.read_text(
                        encoding="utf-8", errors="replace"
                    ).splitlines():
                        self.log(f"[{title}] {line}")
                    for line in stderr_path.read_text(
                        encoding="utf-8", errors="replace"
                    ).splitlines():
                        self.log(f"[{title}][stderr] {line}")
                    completed.add(task.id)
                    self.log(
                        f"[{title}] completed — log: {stdout_path}; err: {stderr_path}"
                    )
        return plan

    def _run_task(
        self,
        task: PlanTask,
        index: int,
        project_root: Path,
    ) -> tuple[str, Path, Path]:
        for directory in task.mkdir_paths:
            Path(directory).mkdir(parents=True, exist_ok=True)
        stdout_path, stderr_path = _task_log_paths(
            project_root, "local", task, index, "local"
        )
        command = task.command
        self.log(
            f"$ {shlex.join(command)}\n"
            f"  stdout -> {stdout_path}\n"
            f"  stderr -> {stderr_path}"
        )
        with stdout_path.open("w", encoding="utf-8") as stdout_handle, \
             stderr_path.open("w", encoding="utf-8") as stderr_handle:
            completed = subprocess.run(
                command,
                cwd=project_root,
                stdout=stdout_handle,
                stderr=stderr_handle,
                text=True,
            )
        if completed.returncode != 0:
            details = _tail(stderr_path) or _tail(stdout_path)
            raise RuntimeError(
                f"{task.title} failed with exit code {completed.returncode}.\n"
                f"log: {stdout_path}\nerr: {stderr_path}\n{details}"
            )
        return task.title, stdout_path, stderr_path


class LSFExecutor:
    """Submit only dependency-ready jobs and monitor them to completion.

    Earlier versions submitted the complete DAG immediately and encoded every
    dependency in ``bsub -w``. A downstream job after a large For Each could
    therefore receive a command line containing thousands of job IDs, and it
    was submitted even when an upstream job would later fail. This executor
    keeps the dependency graph in Analysis Studio instead: it submits root jobs,
    polls LSF, and submits a task only after all parents are actually ``DONE``.
    """

    JOB_ID = re.compile(r"Job <(\d+)>")
    TERMINAL_SUCCESS = {"DONE"}
    TERMINAL_FAILURE = {"EXIT", "ZOMBI", "UNKWN"}

    def __init__(self, log: LogCallback = print) -> None:
        self.log = log

    def run(
        self,
        project: Project,
        project_path: str | Path | None = None,
    ) -> ExecutionPlan:
        root = _project_directory(project_path)
        plan = build_execution_plan(project, root)
        ordered = plan.topological_order()
        poll_seconds = max(1.0, float(project.backend_options.get("lsf_poll_seconds", 10)))
        global_limit = max(0, int(project.backend_options.get("lsf_max_active_jobs", 0) or 0))
        cancel_on_failure = bool(project.backend_options.get("lsf_cancel_on_failure", True))

        pending: dict[str, PlanTask] = {task.id: task for task in ordered}
        active: dict[str, tuple[str, PlanTask]] = {}  # task id -> (job id, task)
        completed: set[str] = set()
        failed: dict[str, tuple[PlanTask, str]] = {}
        missing_polls: Counter[str] = Counter()
        sequence = {task.id: index for index, task in enumerate(ordered)}

        self.log(
            f"Expanded workflow: {len(ordered)} LSF task(s). Dependencies are "
            "monitored by Analysis Studio; bsub -w is not used."
        )

        def stop_after_failure() -> None:
            if not failed:
                return
            if active and cancel_on_failure:
                self._cancel_jobs(job_id for job_id, _task in active.values())
            first_task, reason = next(iter(failed.values()))
            raise RuntimeError(
                f"LSF workflow stopped: {first_task.title}: {reason}. "
                "Downstream jobs were not submitted."
            )

        while pending or active:
            # Never submit another job after any failure has been observed.
            stop_after_failure()
            made_progress = False
            for task_id, task in list(pending.items()):
                if any(dependency in failed for dependency in task.dependencies):
                    parents = [
                        failed[dependency][0].title
                        for dependency in task.dependencies
                        if dependency in failed
                    ]
                    failed[task_id] = (
                        task,
                        "blocked by failed dependency: " + ", ".join(parents),
                    )
                    del pending[task_id]
                    made_progress = True
                    continue
                if not set(task.dependencies).issubset(completed):
                    continue
                if global_limit and len(active) >= global_limit:
                    continue
                job_id = self._submit(
                    task,
                    sequence[task.id],
                    root,
                )
                active[task.id] = (job_id, task)
                del pending[task_id]
                made_progress = True

            stop_after_failure()

            if not active:
                if pending:
                    blocked = ", ".join(task.title for task in pending.values())
                    raise RuntimeError(
                        "No dependency-ready LSF task remains. The workflow is blocked: "
                        + blocked
                    )
                break

            if not made_progress:
                time.sleep(poll_seconds)
            statuses = self._query_statuses(job_id for job_id, _task in active.values())
            for task_id, (job_id, task) in list(active.items()):
                status = statuses.get(job_id)
                if status is None:
                    missing_polls[job_id] += 1
                    if missing_polls[job_id] >= 6:
                        failed[task_id] = (
                            task,
                            f"job {job_id} disappeared from bjobs for six polls",
                        )
                    continue
                missing_polls[job_id] = 0
                state, exit_code = status
                if state in self.TERMINAL_SUCCESS:
                    completed.add(task_id)
                    del active[task_id]
                    self.log(f"[{task.title}] job {job_id} DONE")
                elif state in self.TERMINAL_FAILURE:
                    failed[task_id] = (
                        task,
                        f"job {job_id} ended as {state} (exit code {exit_code or '?'})",
                    )
                    del active[task_id]
            stop_after_failure()

        self.log(f"LSF workflow completed successfully: {len(completed)} job(s).")
        return plan

    def _submit(
        self,
        task: PlanTask,
        index: int,
        project_root: Path,
    ) -> str:
        for directory in task.mkdir_paths:
            Path(directory).mkdir(parents=True, exist_ok=True)
        stdout, stderr = _task_log_paths(
            project_root, "lsf", task, index, "%J"
        )
        queue = task.lsf_queue or "s"
        submit = [
            "bsub",
            "-q",
            queue,
            "-J",
            task.block_name,
            "-o",
            str(stdout),
            "-e",
            str(stderr),
        ]
        submit.extend(task.command)
        self.log(f"$ {shlex.join(submit)}")
        completed = subprocess.run(
            submit,
            cwd=project_root,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        match = self.JOB_ID.search(completed.stdout)
        if not match:
            raise RuntimeError(f"Could not parse LSF job ID: {completed.stdout}")
        job_id = match.group(1)
        self.log(f"[{task.title}] submitted as job {job_id} on queue {queue}")
        return job_id

    def _query_statuses(self, job_ids: Iterable[str]) -> dict[str, tuple[str, str]]:
        ids = list(dict.fromkeys(job_ids))
        result: dict[str, tuple[str, str]] = {}
        for start in range(0, len(ids), 200):
            chunk = ids[start : start + 200]
            command = [
                "bjobs",
                "-a",
                "-noheader",
                "-o",
                "jobid stat exit_code",
                *chunk,
            ]
            completed = subprocess.run(
                command,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            # bjobs may return non-zero when one recently completed ID is no
            # longer retained. Parse any useful rows and let the missing-poll
            # guard handle IDs not present in the output.
            for line in completed.stdout.splitlines():
                parts = line.split()
                if len(parts) < 2 or not parts[0].isdigit():
                    continue
                result[parts[0]] = (parts[1].upper(), parts[2] if len(parts) > 2 else "")
        return result

    def _cancel_jobs(self, job_ids: Iterable[str]) -> None:
        ids = list(dict.fromkeys(job_ids))
        if not ids:
            return
        self.log("Cancelling active LSF jobs after failure: " + ", ".join(ids))
        for start in range(0, len(ids), 200):
            subprocess.run(["bkill", *ids[start : start + 200]], check=False)


class HTCondorExporter:
    def __init__(self, log: LogCallback = print) -> None:
        self.log = log

    @staticmethod
    def _name(text: str) -> str:
        return re.sub(r"[^A-Za-z0-9_]", "_", text)

    @staticmethod
    def _quote_argument(argument: str) -> str:
        return '"' + argument.replace('"', '\\"') + '"'

    def export(
        self,
        project: Project,
        output_directory: str | Path,
        project_path: str | Path | None = None,
    ) -> Path:
        output = Path(output_directory)
        output.mkdir(parents=True, exist_ok=True)
        (output / "logs").mkdir(exist_ok=True)
        scripts = output / "scripts"
        scripts.mkdir(exist_ok=True)
        plan = build_execution_plan(project, _project_directory(project_path))
        universe = str(project.backend_options.get("condor_universe", "vanilla"))
        names: dict[str, str] = {}
        dag_lines: list[str] = []

        for index, task in enumerate(plan.topological_order()):
            job = self._name(f"task_{index:05d}_{task.node_id}")
            names[task.id] = job
            submit_name = f"{job}.sub"
            submit_path = output / submit_name
            wrapper = scripts / f"{job}.sh"
            wrapper_lines = ["#!/usr/bin/env bash", "set -e"]
            for directory in task.mkdir_paths:
                wrapper_lines.append("mkdir -p -- " + shlex.quote(directory))
            wrapper_lines.append(
                "cd -- " + shlex.quote(str(_project_directory(project_path)))
            )
            wrapper_lines.append("exec " + shlex.join(task.command))
            wrapper.write_text("\n".join(wrapper_lines) + "\n", encoding="utf-8")
            wrapper.chmod(0o755)
            lines = [
                f"universe = {universe}",
                f"executable = {wrapper.resolve()}",
                "arguments =",
                f"output = logs/{job}.out",
                f"error = logs/{job}.err",
                f"log = logs/{job}.log",
                "request_cpus = 1",
                "queue 1",
                "",
            ]
            submit_path.write_text("\n".join(lines), encoding="utf-8")
            dag_lines.append(f"JOB {job} {submit_name}")
            parents = [names[parent] for parent in task.dependencies]
            if parents:
                dag_lines.append(f"PARENT {' '.join(parents)} CHILD {job}")

        dag_path = output / "workflow.dag"
        dag_path.write_text("\n".join(dag_lines) + "\n", encoding="utf-8")
        self.log(f"Wrote HTCondor DAG with {len(plan.tasks)} task(s): {dag_path}")
        return dag_path
