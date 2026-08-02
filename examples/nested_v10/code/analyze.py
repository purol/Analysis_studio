from pathlib import Path
import sys

sample = sys.argv[1]
Path("results").mkdir(exist_ok=True)
Path(f"results/{sample}.txt").write_text(f"analyzed {sample}\n", encoding="utf-8")
print(f"analyzed {sample}")
