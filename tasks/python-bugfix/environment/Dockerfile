FROM autoagent-base

WORKDIR /app

RUN uv pip install --system pytest

RUN mkdir -p /app/files /app/output

RUN cat <<'PY' > /app/files/calculator.py
def inclusive_sum(n: int) -> int:
    total = 0
    for value in range(n):
        total += value
    return total
PY

RUN cat <<'PY' > /app/files/test_calculator.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from calculator import inclusive_sum


def test_zero() -> None:
    assert inclusive_sum(0) == 0


def test_one() -> None:
    assert inclusive_sum(1) == 1


def test_four() -> None:
    assert inclusive_sum(4) == 10
PY
