import sys
from pathlib import Path


status = int(sys.argv[1])
log_path = Path("/logs/verifier/pytest.log")
details_path = Path("/logs/verifier/details.txt")
reward_path = Path("/logs/verifier/reward.txt")

log_text = log_path.read_text() if log_path.exists() else ""
passed = status == 0 and "failed" not in log_text.lower()

reward_path.write_text("1" if passed else "0")
details_path.write_text(f"pytest_exit_code={status}\n{log_text}\n")
