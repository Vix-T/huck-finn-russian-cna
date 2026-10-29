#!/bin/bash
# Length-matched mfinder runs: every TEXT_SAMPLE network ([lengthmatched] texts x samples) in parallel, each
# through scripts/run_mfinder.sh with its own seed ([mfinder.lengthmatched].seeds).
#
# Input:  data/interim/networks/TEXT_SAMPLE_mfinder.txt   (uv run --frozen python -m huckfinn_cna.lengthmatched)
# Output: data/interim/mfinder/lengthmatched/TEXT_SAMPLE_r{R}/        one run folder per sample
#         data/interim/mfinder/lengthmatched/TEXT_SAMPLE_r{R}.console.txt
#         data/interim/mfinder/lengthmatched/run_lengthmatched_r{R}.log   launch, PIDs, exit status of each sample
# Refuses to start unless every network matches docs/networks/checksums.txt (networks-v1 and the length-matched
# section) and no TEXT_SAMPLE_r{R} folder exists. Progress: scripts/mfinder_status.sh R --set lengthmatched
#
# Usage: scripts/run_mfinder_lengthmatched.sh R      e.g. scripts/run_mfinder_lengthmatched.sh 1000
set -euo pipefail

if [ $# -ne 1 ] || ! [[ $1 =~ ^[0-9]+$ ]]; then
    echo "usage: $0 R   (R = number of random networks)" >&2
    exit 2
fi
r=$1
repo="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo"

# Line 1: output root; line 2: checksums file; then one "NAME SEED" line per sample.
config_values=$(uv run --frozen python -c '
from huckfinn_cna.config import load_config
c = load_config()
print(c["paths"]["mfinder_lengthmatched_dir"], c["paths"]["network_checksums"], sep="\n")
for name, seed in c["mfinder"]["lengthmatched"]["seeds"].items():
    print(name, seed)
')
{ read -r out_root; read -r checksums; } <<< "$config_values"
names=() seeds=()
while read -r name seed; do names+=("$name"); seeds+=("$seed"); done < <(tail -n +3 <<< "$config_values")

if ! shasum -a 256 -c --quiet "$checksums"; then
    echo "networks do not match $checksums; not starting" >&2
    exit 2
fi
for name in "${names[@]}"; do
    if [ -e "$out_root/${name}_r${r}" ]; then
        echo "$out_root/${name}_r${r} already exists; not starting any sample" >&2
        exit 2
    fi
done
mkdir -p "$out_root"

log="$out_root/run_lengthmatched_r${r}.log"
echo "run_mfinder_lengthmatched R=$r start: $(date '+%Y-%m-%d %H:%M:%S %z')  commit $(git rev-parse HEAD)" | tee "$log"
pids=()
for i in "${!names[@]}"; do
    name=${names[$i]} seed=${seeds[$i]}
    scripts/run_mfinder.sh "$name" "$r" "$seed" "$out_root/${name}_r${r}" \
        > "$out_root/${name}_r${r}.console.txt" 2>&1 &
    pids+=($!)
    echo "$name: seed $seed, pid $!, output $out_root/${name}_r${r}" | tee -a "$log"
done

failed=0
for i in "${!names[@]}"; do
    if wait "${pids[$i]}"; then status=0; else status=$?; failed=1; fi
    echo "${names[$i]}: exit status $status (end time in its run.log)" | tee -a "$log"
done
echo "run_mfinder_lengthmatched R=$r end: $(date '+%Y-%m-%d %H:%M:%S %z')" | tee -a "$log"
exit $failed
