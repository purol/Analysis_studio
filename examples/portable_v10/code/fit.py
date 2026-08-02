from pathlib import Path

results = sorted(Path("results").glob("*.txt"))
Path("fit").mkdir(exist_ok=True)
Path("fit/summary.txt").write_text(
    "".join(path.read_text(encoding="utf-8") for path in results),
    encoding="utf-8",
)
print(f"combined {len(results)} result(s)")
