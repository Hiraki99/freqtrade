#!/usr/bin/env bash
# Nạp .env rồi chạy SMC Analysis bot (rule-based, miễn phí).
# Usage: ./run_ta_analysis.sh
set -euo pipefail
cd "$(dirname "$0")"
if [[ -f .env ]]; then set -a; source .env; set +a; fi
PY="python3"; [[ -x .venv/bin/python ]] && PY=".venv/bin/python"
exec "$PY" ta_analysis.py
