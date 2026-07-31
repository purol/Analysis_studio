from __future__ import annotations

from collections import Counter
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
import re
import shlex
import subprocess
from typing import Callable

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
    JOB_ID = re.compile(r"Job <(\d+)>")

    def __init__(self, queue: str = "s", log: LogCallback = print) -> None:
        self.queue = queue
        self.log = log

    def run(
        self,
        project: Project,
        project_path: str | Path | None = None,
    ) -> ExecutionPlan:
        plan = build_execution_plan(project, _project_directory(project_path))
        log_root = Path(".analysis-studio") / "lsf-logs"
        log_root.mkdir(parents=True, exist_ok=True)
        job_ids: dict[str, str] = {}

        for index, task in enumerate(plan.topological_order()):
            if task.output_dir:
                Path(task.output_dir).mkdir(parents=True, exist_ok=True)
            stdout = log_root / f"{index:05d}_{task.node_id}.out"
            stderr = log_root / f"{index:05d}_{task.node_id}.err"
            submit = [
                "bsub",
                "-q",
                self.queue,
                "-J",
                task.job_name,
                "-o",
                str(stdout),
                "-e",
                str(stderr),
            ]
            if task.working_directory:
                submit.extend(["-cwd", task.working_directory])
            dependency_ids = [job_ids[item] for item in task.dependencies]
            if dependency_ids:
                expression = " && ".join(f"done({job_id})" for job_id in dependency_ids)
                submit.extend(["-w", expression])
            submit.extend(task.command)
            self.log(f"$ {shlex.join(submit)}")
            completed = subprocess.run(
                submit,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            match = self.JOB_ID.search(completed.stdout)
            if not match:
                raise RuntimeError(f"Could not parse LSF job ID: {completed.stdout}")
            job_ids[task.id] = match.group(1)
            self.log(f"[{task.title}] submitted as job {match.group(1)}")
        return plan


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
