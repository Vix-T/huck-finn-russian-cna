#!/bin/bash
# Final mfinder run: all five texts in parallel, each through scripts/run_mfinder.sh with its own seed.
#
# Texts, seeds ([mfinder.final].seeds, from `uv run python -m huckfinn_cna.mfinder_seeds`) and the output
# root ([paths].mfinder_final_dir) come from config/pipeline.toml.
# Output: data/interim/mfinder/final/TEXT_r{R}/   (one run folder per text; see run_mfinder.sh)
#         data/interim/mfinder/final/TEXT_r{R}.console.txt   what run_mfinder.sh printed
#         data/interim/mfinder/final/run_all_r{R}.log        launch, PIDs, exit status of each text
# Refuses to start unless the networks match docs/networks/checksums.txt and no TEXT_r{R} folder exists.
# Waits for all five; exits non-zero if any text failed. Progress: scripts/mfinder_status.sh R
#
# Usage: scripts/run_mfinder_all.sh R      e.g. scripts/run_mfinder_all.sh 1000
set -euo pipefail

if [ $# -ne 1 ] || ! [[ $1 =~ ^[0-9]+$ ]]; then
    echo "usage: $0 R   (R = number of random networks)" >&2
    exit 2
fi
r=$1
repo="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo"

# Line 1: final output root; line 2: checksums file; then one "TEXT SEED" line per text.
config_values=$(uv run --frozen python -c '
from huckfinn_cna.config import load_config
c = load_config()
print(c["paths"]["mfinder_final_dir"], c["paths"]["network_checksums"], sep="\n")
for text in c["corpus"]["texts"]:
    print(text, c["mfinder"]["final"]["seeds"][text])
')
{ read -r final_root; read -r checksums; } <<< "$config_values"
texts=() seeds=()
while read -r text seed; do texts+=("$text"); seeds+=("$seed"); done < <(tail -n +3 <<< "$config_values")

if ! shasum -a 256 -c --quiet "$checksums"; then
    echo "networks do not match $checksums; not starting" >&2
    exit 2
fi
for text in "${texts[@]}"; do
    if [ -e "$final_root/${text}_r${r}" ]; then
        echo "$final_root/${text}_r${r} already exists; not starting any text" >&2
        exit 2
    fi
done
mkdir -p "$final_root"

log="$final_root/run_all_r${r}.log"
echo "run_mfinder_all R=$r start: $(date '+%Y-%m-%d %H:%M:%S %z')  commit $(git rev-parse HEAD)" | tee "$log"
pids=()
for i in "${!texts[@]}"; do
    text=${texts[$i]} seed=${seeds[$i]}
    scripts/run_mfinder.sh "$text" "$r" "$seed" "$final_root/${text}_r${r}" \
        > "$final_root/${text}_r${r}.console.txt" 2>&1 &
    pids+=($!)
    echo "$text: seed $seed, pid $!, output $final_root/${text}_r${r}" | tee -a "$log"
done

failed=0
for i in "${!texts[@]}"; do
    if wait "${pids[$i]}"; then status=0; else status=$?; failed=1; fi
    echo "${texts[$i]}: exit status $status (end time in its run.log)" | tee -a "$log"
done
echo "run_mfinder_all R=$r end: $(date '+%Y-%m-%d %H:%M:%S %z')" | tee -a "$log"
exit $failed
