#!/bin/bash
# Run mfinder (tools/mfinder/bin/mfinder, built by tools/mfinder/build.sh) on one text's network.
#
# Input:  data/interim/networks/TEXT_mfinder.txt   (from `uv run python -m huckfinn_cna.networks`)
# Output: OUT_DIR, default data/interim/mfinder/TEXT_r{R}_s{SEED}/
#           TEXT_r{R}_s{SEED}_OUT.txt     mfinder summary (NREAL, random mean and SD, Z, p)
#           TEXT_r{R}_s{SEED}_MAT_R.txt   count of every subgraph in every random network (-orall)
#           TEXT_r{R}_s{SEED}_INTER.txt   results after each random network (-oi; progress)
#           mfinder_console.txt           anything mfinder printed (normally nothing: -q)
#           run.log                       command, checksums, start and end times, exit code
# Paths, motif size and flags come from config/pipeline.toml ([paths], [networks], [mfinder]).
# An existing output directory is never overwritten.
#
# Usage: scripts/run_mfinder.sh TEXT R SEED [OUT_DIR]      e.g. scripts/run_mfinder.sh Ru1 100 20261021
set -euo pipefail

if [ $# -lt 3 ] || [ $# -gt 4 ]; then
    echo "usage: $0 TEXT R SEED [OUT_DIR]" >&2
    exit 2
fi
text=$1 r=$2 seed=$3 out_arg=${4:-}
repo="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo"

# One line per value: binary, networks dir, output root, motif size, seed variable, seed range, extra flags.
config_values=$(uv run --frozen python -c '
from huckfinn_cna.config import load_config
c = load_config()
m = c["mfinder"]
print(m["binary"], c["paths"]["networks_dir"], c["paths"]["mfinder_out_dir"], c["networks"]["motif_size"],
      m["seed_env"], m["seed_min"], m["seed_max"], sep="\n")
print(" ".join(m["extra_flags"]))
')
{ read -r binary; read -r networks_dir; read -r out_root; read -r motif_size
  read -r seed_env; read -r seed_min; read -r seed_max; read -r -a extra_flags; } <<< "$config_values"

if ! [[ $r =~ ^[0-9]+$ ]]; then echo "R must be a non-negative integer: $r" >&2; exit 2; fi
if ! [[ $seed =~ ^[0-9]+$ ]] || [ "$seed" -lt "$seed_min" ] || [ "$seed" -gt "$seed_max" ]; then
    echo "SEED must be an integer in $seed_min..$seed_max: $seed" >&2; exit 2
fi
input="$networks_dir/${text}_mfinder.txt"
[ -f "$input" ] || { echo "no network file $input (run: uv run python -m huckfinn_cna.networks)" >&2; exit 2; }
[ -x "$binary" ] || { echo "no mfinder binary $binary (run: tools/mfinder/build.sh)" >&2; exit 2; }

run_name="${text}_r${r}_s${seed}"
out=${out_arg:-$out_root/$run_name}
if [ -e "$out" ]; then echo "$out already exists; not overwriting" >&2; exit 2; fi
mkdir -p "$out"

cmd=("$binary" "$input" -s "$motif_size" -r "$r" -f "$out/$run_name" "${extra_flags[@]}")
log="$out/run.log"
dirty=$(git status --porcelain --untracked-files=no | grep -q . && echo " (uncommitted changes)" || true)
{
    echo "text: $text"
    echo "random networks: $r"
    echo "seed: $seed ($seed_env)"
    echo "command: $seed_env=$seed $(printf '%q ' "${cmd[@]}")"
    echo "binary sha256: $(shasum -a 256 "$binary" | cut -d' ' -f1)"
    echo "input sha256: $(shasum -a 256 "$input" | cut -d' ' -f1)"
    echo "repository commit: $(git rev-parse HEAD)$dirty"
    echo "host: $(uname -mrs)"
    echo "start: $(date '+%Y-%m-%d %H:%M:%S %z')"
} > "$log"
cat "$log"

start=$(date +%s)
set +e
env "$seed_env=$seed" "${cmd[@]}" > "$out/mfinder_console.txt" 2>&1
status=$?
set -e
end=$(date +%s)
{
    echo "end: $(date '+%Y-%m-%d %H:%M:%S %z')"
    echo "elapsed seconds: $((end - start))"
    echo "exit status: $status"
} | tee -a "$log"
exit $status
