#!/bin/bash
# Progress of the final mfinder runs started by scripts/run_mfinder_all.sh: for each text, random
# networks done out of R, elapsed time and estimated finish (src/huckfinn_cna/mfinder_status.py).
#
# Usage: scripts/mfinder_status.sh [R]      (default R: [mfinder.final].r in config/pipeline.toml)
#        watch every 10 minutes:  while true; do clear; scripts/mfinder_status.sh 1000; sleep 600; done
set -euo pipefail
cd "$(dirname "$0")/.."
exec uv run --frozen python -m huckfinn_cna.mfinder_status "$@"
