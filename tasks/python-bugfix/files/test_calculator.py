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
