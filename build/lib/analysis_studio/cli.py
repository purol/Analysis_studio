from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .build import compile_project, ensure_build_current, generate_code
from .execution import HTCondorExporter, LSFExecutor, LocalExecutor
from .model import Project
from .validation import validate_project


def _load(path: str) -> tuple[Path, Project]:
    project_path = Path(path).resolve()
    if not project_path.exists():
        raise FileNotFoundError(project_path)
    return project_path, Project.load(project_path)


def _validate_or_raise(project: Project, project_path: Path) -> None:
    errors = validate_project(project, project_path.parent)
    if errors:
        raise ValueError("Project validation failed:\n" + "\n".join(errors))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="analysis-studio-cli",
        description=(
            "Headless Generate Code, Compile and Run/Submit commands for an "
            "Analysis Studio .astudio.json project."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("validate", "generate", "compile"):
        command = sub.add_parser(name)
        command.add_argument("project")

    run = sub.add_parser("run")
    run.add_argument("project")
    run.add_argument("--backend", choices=("local", "lsf"), default=None)

    submit = sub.add_parser("submit")
    submit.add_argument("project")
    submit.add_argument("--backend", choices=("lsf",), default="lsf")

    condor = sub.add_parser("export-condor")
    condor.add_argument("project")
    condor.add_argument("--output", default=None)

    pipeline = sub.add_parser("pipeline")
    pipeline.add_argument("project")
    pipeline.add_argument("--backend", choices=("local", "lsf"), default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        project_path, project = _load(args.project)
        _validate_or_raise(project, project_path)

        if args.command == "validate":
            print("Project is valid.")
            return 0
        if args.command == "generate":
            generate_code(project, project_path)
            return 0
        if args.command == "compile":
            result = compile_project(project, project_path)
            print(f"Built {len(result.outputs)} artifact(s).")
            return 0
        if args.command == "pipeline":
            generate_code(project, project_path)
            compile_project(project, project_path)
            backend = args.backend or project.backend
        elif args.command in {"run", "submit"}:
            backend = args.backend or project.backend
        elif args.command == "export-condor":
            ensure_build_current(project_path)
            output = Path(args.output).resolve() if args.output else project_path.parent / "condor"
            HTCondorExporter().export(project, output, project_path)
            return 0
        else:
            parser.error(f"Unsupported command: {args.command}")
            return 2

        ensure_build_current(project_path)
        if backend == "local":
            LocalExecutor().run(project, project_path)
        elif backend == "lsf":
            LSFExecutor().run(project, project_path)
        else:
            raise ValueError(
                f"Backend '{backend}' is not directly runnable. Use export-condor for HTCondor."
            )
        return 0
    except Exception as exc:
        print(f"analysis-studio-cli: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
