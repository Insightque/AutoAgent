from pathlib import Path


input_path = Path("/app/files/input.txt")
result_path = Path("/app/output/result.txt")
details_path = Path("/logs/verifier/details.txt")
reward_path = Path("/logs/verifier/reward.txt")

checks = []

checks.append(("input_exists", input_path.exists()))
checks.append(("result_exists", result_path.exists()))

if input_path.exists():
    checks.append(("input_content", input_path.read_text() == "DONE\n"))
if result_path.exists():
    checks.append(("result_content", result_path.read_text() == "task completed"))

passed = all(passed for _, passed in checks)
reward_path.write_text("1" if passed else "0")
details_path.write_text(
    "\n".join(f"{name}={passed}" for name, passed in checks) + "\n"
)
