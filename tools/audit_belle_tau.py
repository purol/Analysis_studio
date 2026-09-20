"""Usage: python tools/audit_belle_tau.py ../Belle_tau/analysis_code/src"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from analysis_studio.coverage import audit_loader_calls

parser = argparse.ArgumentParser()
parser.add_argument("source", type=Path)
parser.add_argument("--output", type=Path, default=Path("docs/belle_tau_loader_coverage.json"))
args = parser.parse_args()
if not args.source.is_dir():
    parser.error(f"source directory does not exist: {args.source}")
report = audit_loader_calls(args.source)
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
unmapped = [r["method"] for r in report["methods"] if not r["module"]]
print(f"{report['files_scanned']} files; {len(report['methods'])} Loader methods; unmapped: {unmapped}")
