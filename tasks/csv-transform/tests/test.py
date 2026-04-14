import json
from pathlib import Path


summary_path = Path("/app/output/summary.json")
details_path = Path("/logs/verifier/details.txt")
reward_path = Path("/logs/verifier/reward.txt")

expected = {
    "row_count": 4,
    "total_amount": 100,
    "average_amount": 25,
    "max_amount": 40,
    "min_amount": 10,
}

checks = [("summary_exists", summary_path.exists())]

if summary_path.exists():
    try:
        actual = json.loads(summary_path.read_text())
        checks.append(("summary_exact", actual == expected))
    except json.JSONDecodeError:
        checks.append(("summary_exact", False))

passed = all(passed for _, passed in checks)
reward_path.write_text("1" if passed else "0")
details_path.write_text(
    "\n".join(f"{name}={passed}" for name, passed in checks) + "\n"
)
