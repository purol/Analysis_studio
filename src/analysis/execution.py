from __future__ import annotations

from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import glob
import os
import re
import shlex
import subprocess
from typing import Callable

from .model import Graph, Project, WorkflowNode


LogCallback = Callable[[str], None]


@dataclass
class StageResult:
    paths: list[str]
    directory: str = ""
    job_ids: list[str] | None = None


@dataclass
class CommandInstance:
    node_id: str
    title: str
    executable: str
    arguments: list[str]
    output_dir: str
    dependency_ids: list[str]

    @property
    def argv(self) -> list[str]:
        return [self.executable, *self.arguments]

    def display(self) -> str:
        return shlex.join(self.argv)


def _upstream_results(
    graph: Graph,
    results: dict[str, StageResult],
    node_id: str,
) -> list[StageResult]:
    return [results[edge.source] for edge in graph.incoming(node_id) if edge.source in results]


def _format_arguments(template: str, input_path: str, input_dir: str, output_dir: str, inputs: list[str]) -> list[str]:
    input_object = Path(input_path) if input_path else Path(input_dir or ".")
    values = {
        "input": input_path,
        "input_dir": input_dir,
        "filename": input_object.name,
        "stem": input_object.stem,
        "output_dir": output_dir,
        "inputs": " ".join(inputs),
    }
    try:
        expanded = template.format(**values)
    except KeyError as exc:
        raise ValueError(f"Unknown argument token: {exc.args[0]}") from exc
    return shlex.split(expanded)


def source_result(node: WorkflowNode) -> StageResult:
    directory = os.path.expandvars(os.path.expanduser(str(node.properties["directory"])))
    pattern = str(node.properties.get("pattern", "*.root"))
    paths = sorted(glob.glob(str(Path(directory) / pattern)))
    return StageResult(paths=paths, directory=directory, job_ids=[])


def command_instances(
    graph: Graph,
    node: WorkflowNode,
    results: dict[str, StageResult],
) -> list[CommandInstance]:
    upstream = _upstream_results(graph, results, node.id)
    all_paths = [path for result in upstream for path in result.paths]
    input_dir = next((result.directory for result in upstream if result.directory), "")
    dependencies = [
        job_id
        for result in upstream
        for job_id in (result.job_ids or [])
    ]

    executable = os.path.expandvars(os.path.expanduser(str(node.properties["executable"])))
    template = str(node.properties.get("arguments", ""))
    output_dir = os.path.expandvars(
        os.path.expanduser(str(node.properties.get("output_dir", "")))
    )
    run_mode = str(node.properties.get("run_mode", "once"))
    if run_mode == "per_file":
        return [
            CommandInstance(
                node_id=node.id,
                title=f"{node.title}: {Path(path).name}",
                executable=executable,
                arguments=_format_arguments(template, path, str(Path(path).parent), output_dir, all_paths),
                output_dir=output_dir,
                dependency_ids=dependencies,
            )
            for path in all_paths
        ]
    return [
        CommandInstance(
            node_id=node.id,
            title=node.title,
            executable=executable,
            arguments=_format_arguments(template, "", input_dir, output_dir, all_paths),
            output_dir=output_dir,
            dependency_ids=dependencies,
        )
    ]


class LocalExecutor:
    def __init__(self, log: LogCallback = print) -> None:
        self.log = log

    def run(self, project: Project) -> dict[str, StageResult]:
        graph = project.workflow
        results: dict[str, StageResult] = {}
        for node in graph.topological_order():
            if node.type == "root_files":
                result = source_result(node)
                self.log(f"[{node.title}] found {len(result.paths)} input file(s)")
                results[node.id] = result
                continue

            upstream = _upstream_results(graph, results, node.id)
            if node.type == "join":
                results[node.id] = StageResult(
                    paths=[path for result in upstream for path in result.paths],
                    directory=next(
                        (result.directory for result in upstream if result.directory),
                        "",
                    ),
                    job_ids=[],
                )
                continue

            instances = command_instances(graph, node, results)
            if not instances:
                raise RuntimeError(f"{node.title}: no command instances were created.")
            workers = max(1, int(project.backend_options.get("local_workers", 1)))
            if len(instances) > 1 and workers > 1:
                self.log(
                    f"[{node.title}] running {len(instances)} command(s) "
                    f"with {workers} local workers"
                )
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = {
                        pool.submit(self._run_instance, instance): instance
                        for instance in instances
                    }
                    for future in as_completed(futures):
                        title, output = future.result()
                        for line in output.splitlines():
                            self.log(f"[{title}] {line}")
            else:
                for instance in instances:
                    title, output = self._run_instance(instance)
                    for line in output.splitlines():
                        self.log(f"[{title}] {line}")

            output_dir = str(node.properties.get("output_dir", ""))
            if node.type == "validator" and not output_dir and upstream:
                results[node.id] = StageResult(
                    paths=[path for result in upstream for path in result.paths],
                    directory=upstream[0].directory,
                    job_ids=[],
                )
            else:
                results[node.id] = StageResult(
                    paths=sorted(glob.glob(str(Path(output_dir) / "*.root")))
                    if output_dir
                    else [],
                    directory=output_dir,
                    job_ids=[],
                )
            self.log(f"[{node.title}] completed")
        return results

    def _run_instance(self, instance: CommandInstance) -> tuple[str, str]:
        if instance.output_dir:
            Path(instance.output_dir).mkdir(parents=True, exist_ok=True)
        self.log(f"$ {instance.display()}")
        completed = subprocess.run(
            instance.argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"{instance.title} failed with exit code {completed.returncode}.\n"
                f"{completed.stdout}"
            )
        return instance.title, completed.stdout


class LSFExecutor:
    JOB_ID = re.compile(r"Job <(\d+)>")

    def __init__(self, queue: str = "s", log: LogCallback = print) -> None:
        self.queue = queue
        self.log = log

    def run(self, project: Project) -> dict[str, StageResult]:
        graph = project.workflow
        results: dict[str, StageResult] = {}
        log_root = Path(".belleflow") / "lsf-logs"
        log_root.mkdir(parents=True, exist_ok=True)

        for node in graph.topological_order():
            if node.type == "root_files":
                results[node.id] = source_result(node)
                continue
            upstream = _upstream_results(graph, results, node.id)
            if node.type == "join":
                results[node.id] = StageResult(
                    paths=[path for result in upstream for path in result.paths],
                    directory=next((r.directory for r in upstream if r.directory), ""),
                    job_ids=[jid for r in upstream for jid in (r.job_ids or [])],
                )
                continue

            submitted: list[str] = []
            for index, instance in enumerate(command_instances(graph, node, results)):
                if instance.output_dir:
                    Path(instance.output_dir).mkdir(parents=True, exist_ok=True)
                job_name = str(node.properties.get("job_name", node.title))
                stdout = log_root / f"{node.id}_{index}.out"
                stderr = log_root / f"{node.id}_{index}.err"
                submit = [
                    "bsub",
                    "-q",
                    self.queue,
                    "-J",
                    job_name,
                    "-o",
                    str(stdout),
                    "-e",
                    str(stderr),
                ]
                if instance.dependency_ids:
                    expression = " && ".join(
                        f"done({job_id})" for job_id in instance.dependency_ids
                    )
                    submit.extend(["-w", expression])
                submit.extend(instance.argv)
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
                submitted.append(match.group(1))

            output_dir = str(node.properties.get("output_dir", ""))
            results[node.id] = StageResult(
                paths=sorted(glob.glob(str(Path(output_dir) / "*.root")))
                if output_dir
                else [p for r in upstream for p in r.paths],
                directory=output_dir or next(
                    (r.directory for r in upstream if r.directory), ""
                ),
                job_ids=submitted,
            )
            self.log(f"[{node.title}] submitted job(s): {', '.join(submitted)}")
        return results


class HTCondorExporter:
    def __init__(self, log: LogCallback = print) -> None:
        self.log = log

    @staticmethod
    def _name(text: str) -> str:
        return re.sub(r"[^A-Za-z0-9_]", "_", text)

    def export(self, project: Project, output_directory: str | Path) -> Path:
        output = Path(output_directory)
        output.mkdir(parents=True, exist_ok=True)
        graph = project.workflow
        results: dict[str, StageResult] = {}
        node_jobs: dict[str, list[str]] = {}
        dag_lines: list[str] = []
        universe = str(project.backend_options.get("condor_universe", "vanilla"))

        for node in graph.topological_order():
            if node.type == "root_files":
                results[node.id] = source_result(node)
                node_jobs[node.id] = []
                continue
            upstream = _upstream_results(graph, results, node.id)
            if node.type == "join":
                results[node.id] = StageResult(
                    paths=[p for r in upstream for p in r.paths],
                    directory=next((r.directory for r in upstream if r.directory), ""),
                    job_ids=[],
                )
                node_jobs[node.id] = [
                    job for edge in graph.incoming(node.id) for job in node_jobs[edge.source]
                ]
                continue

            jobs: list[str] = []
            instances = command_instances(graph, node, results)
            for index, instance in enumerate(instances):
                job = self._name(f"{node.id}_{index}")
                submit_name = f"{job}.sub"
                submit_path = output / submit_name
                arguments = " ".join(
                    '"' + arg.replace('"', '\\"') + '"' for arg in instance.arguments
                )
                submit_path.write_text(
                    "\n".join(
                        [
                            f"universe = {universe}",
                            f"executable = {instance.executable}",
                            f"arguments = {arguments}",
                            f"output = logs/{job}.out",
                            f"error = logs/{job}.err",
                            f"log = logs/{job}.log",
                            "request_cpus = 1",
                            "queue 1",
                            "",
                        ]
                    ),
                    encoding="utf-8",
                )
                dag_lines.append(f"JOB {job} {submit_name}")
                parents = [
                    parent
                    for edge in graph.incoming(node.id)
                    for parent in node_jobs.get(edge.source, [])
                ]
                if parents:
                    dag_lines.append(
                        f"PARENT {' '.join(parents)} CHILD {job}"
                    )
                jobs.append(job)

            node_jobs[node.id] = jobs
            output_dir = str(node.properties.get("output_dir", ""))
            results[node.id] = StageResult(
                paths=sorted(glob.glob(str(Path(output_dir) / "*.root")))
                if output_dir
                else [p for r in upstream for p in r.paths],
                directory=output_dir or next((r.directory for r in upstream if r.directory), ""),
                job_ids=[],
            )

        (output / "logs").mkdir(exist_ok=True)
        dag_path = output / "workflow.dag"
        dag_path.write_text("\n".join(dag_lines) + "\n", encoding="utf-8")
        self.log(f"Wrote HTCondor DAG: {dag_path}")
        return dag_path
