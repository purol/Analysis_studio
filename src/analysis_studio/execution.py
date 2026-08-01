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


class LocalExecutor:
    def __init__(self, log: LogCallback = print) -> None:
        self.log = log

    def run(
        self,
        project: Project,
        project_path: str | Path | None = None,
    ) -> ExecutionPlan:
        plan = build_execution_plan(project, _project_directory(project_path))
        workers = max(1, int(project.backend_options.get("local_workers", 1)))
        self.log(f"Expanded workflow: {len(plan.tasks)} command task(s)")
        if not plan.tasks:
            return plan

        pending = {task.id: task for task in plan.tasks}
        completed: set[str] = set()
        running: dict[Future[tuple[str, str]], PlanTask] = {}
        active_limits: Counter[str] = Counter()

        def allowed(task: PlanTask) -> bool:
            if not set(task.dependencies).issubset(completed):
                return False
            return all(
                active_limits[group] < maximum
                for group, maximum in task.concurrency_limits.items()
            )

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
                        for group in task.concurrency_limits:
                            active_limits[group] += 1
                        running[pool.submit(self._run_task, task)] = task
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
                    for group in task.concurrency_limits:
                        active_limits[group] -= 1
                    try:
                        title, output = future.result()
                    except Exception:
                        for other in running:
                            other.cancel()
                        raise
                    for line in output.splitlines():
                        self.log(f"[{title}] {line}")
                    completed.add(task.id)
                    self.log(f"[{title}] completed")
        return plan

    def _run_task(self, task: PlanTask) -> tuple[str, str]:
        if task.output_dir:
            Path(task.output_dir).mkdir(parents=True, exist_ok=True)
        command = task.command
        self.log(f"$ {shlex.join(command)}")
        completed = subprocess.run(
            command,
            cwd=task.working_directory or None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"{task.title} failed with exit code {completed.returncode}.\n"
                f"{completed.stdout}"
            )
        return task.title, completed.stdout


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

    def __init__(self, queue: str = "s", log: LogCallback = print) -> None:
        self.queue = queue
        self.log = log

    def run(
        self,
        project: Project,
        project_path: str | Path | None = None,
    ) -> ExecutionPlan:
        root = _project_directory(project_path)
        plan = build_execution_plan(project, root)
        ordered = plan.topological_order()
        log_root = root / ".analysis-studio" / "lsf-logs"
        log_root.mkdir(parents=True, exist_ok=True)

        poll_seconds = max(1.0, float(project.backend_options.get("lsf_poll_seconds", 10)))
        global_limit = max(0, int(project.backend_options.get("lsf_max_inflight", 0) or 0))
        cancel_on_failure = bool(project.backend_options.get("lsf_cancel_on_failure", True))

        pending: dict[str, PlanTask] = {task.id: task for task in ordered}
        active: dict[str, tuple[str, PlanTask]] = {}  # task id -> (job id, task)
        completed: set[str] = set()
        failed: dict[str, tuple[PlanTask, str]] = {}
        active_by_node: Counter[str] = Counter()
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
                node_limit = task.lsf_max_inflight
                if node_limit and active_by_node[task.node_id] >= node_limit:
                    continue

                job_id = self._submit(
                    task,
                    sequence[task.id],
                    log_root,
                    root,
                    default_queue=str(project.backend_options.get("lsf_queue", self.queue)),
                )
                active[task.id] = (job_id, task)
                active_by_node[task.node_id] += 1
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
                    active_by_node[task.node_id] -= 1
                    self.log(f"[{task.title}] job {job_id} DONE")
                elif state in self.TERMINAL_FAILURE:
                    failed[task_id] = (
                        task,
                        f"job {job_id} ended as {state} (exit code {exit_code or '?'})",
                    )
                    del active[task_id]
                    active_by_node[task.node_id] -= 1
            stop_after_failure()

        self.log(f"LSF workflow completed successfully: {len(completed)} job(s).")
        return plan

    def _submit(
        self,
        task: PlanTask,
        index: int,
        log_root: Path,
        project_root: Path,
        default_queue: str,
    ) -> str:
        if task.output_dir:
            Path(task.output_dir).mkdir(parents=True, exist_ok=True)
        stdout = log_root / f"{index:05d}_{task.node_id}.out"
        stderr = log_root / f"{index:05d}_{task.node_id}.err"
        queue = task.lsf_queue or default_queue or self.queue
        submit = [
            "bsub",
            "-q",
            queue,
            "-J",
            task.job_name,
            "-o",
            str(stdout),
            "-e",
            str(stderr),
        ]
        if task.working_directory:
            submit.extend(["-cwd", task.working_directory])
        submit.extend(task.lsf_extra_options)
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
        plan = build_execution_plan(project, _project_directory(project_path))
        universe = str(project.backend_options.get("condor_universe", "vanilla"))
        names: dict[str, str] = {}
        dag_lines: list[str] = []

        for index, task in enumerate(plan.topological_order()):
            job = self._name(f"task_{index:05d}_{task.node_id}")
            names[task.id] = job
            submit_name = f"{job}.sub"
            submit_path = output / submit_name
            arguments = " ".join(self._quote_argument(arg) for arg in task.argv)
            lines = [
                f"universe = {universe}",
                f"executable = {task.executable}",
                f"arguments = {arguments}",
                f"output = logs/{job}.out",
                f"error = logs/{job}.err",
                f"log = logs/{job}.log",
                "request_cpus = 1",
            ]
            if task.working_directory:
                lines.append(f"initialdir = {task.working_directory}")
            lines.extend(["queue 1", ""])
            submit_path.write_text("\n".join(lines), encoding="utf-8")
            dag_lines.append(f"JOB {job} {submit_name}")
            parents = [names[parent] for parent in task.dependencies]
            if parents:
                dag_lines.append(f"PARENT {' '.join(parents)} CHILD {job}")

        dag_path = output / "workflow.dag"
        dag_path.write_text("\n".join(dag_lines) + "\n", encoding="utf-8")
        self.log(f"Wrote HTCondor DAG with {len(plan.tasks)} task(s): {dag_path}")
        return dag_path
