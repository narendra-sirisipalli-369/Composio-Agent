"""Reproduce apps.json from the official assignment, preserving its hints verbatim."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).parent
source = (ROOT / "assignment.md").read_text()
category = None
apps = []
for line in source.splitlines():
    heading = re.match(r"^### \d+\. (.+)$", line)
    if heading:
        category = heading.group(1)
    row = re.match(r"^\| (\d{1,3}) \| (.+?) \| (.+?) \|$", line)
    if row and category:
        number, name, hint = row.groups()
        apps.append({"id": int(number), "name": name, "category": category, "hint": hint})

assert len(apps) == 100, f"Expected 100 apps, got {len(apps)}"
assert [app["id"] for app in apps] == list(range(1, 101))
(ROOT / "apps.json").write_text(json.dumps(apps, indent=2, ensure_ascii=False) + "\n")
print("Wrote 100 apps from assignment.md")
