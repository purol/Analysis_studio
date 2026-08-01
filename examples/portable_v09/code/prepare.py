from pathlib import Path

Path("state").mkdir(exist_ok=True)
Path("state/prepared.txt").write_text("ready\n", encoding="utf-8")
print("prepared")
