#!/bin/bash
set -euo pipefail

status=0
pytest -q /app/files/test_calculator.py > /logs/verifier/pytest.log 2>&1 || status=$?
python /tests/test.py "$status"
