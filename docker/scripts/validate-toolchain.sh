#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKER_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "[check] shell syntax"
bash -n "${DOCKER_DIR}/scripts/entrypoint.sh"
bash -n "${DOCKER_DIR}/scripts/init-wizard.sh"
bash -n "${DOCKER_DIR}/scripts/langgraph-trigger"
bash -n "${DOCKER_DIR}/scripts/langgraph-trigger-photo-sorter"
bash -n "${DOCKER_DIR}/scripts/langgraph-resume"

echo "[check] python syntax"
python3 -m py_compile "${DOCKER_DIR}/templates/langgraph-src/auto_programming.py"
python3 -m py_compile "${DOCKER_DIR}/templates/langgraph-src/memory.py"
python3 -m py_compile "${DOCKER_DIR}/templates/langgraph-src/photo_sorter.py"

echo "[check] json templates"
python3 - "${DOCKER_DIR}" <<'PY'
import json
import sys
from pathlib import Path

base = Path(sys.argv[1]) / "templates"
for rel in ["opencode-config.json", "langgraph.json"]:
    p = base / rel
    with p.open("r", encoding="utf-8") as f:
        json.load(f)
print("ok")
PY

echo "All checks passed."
