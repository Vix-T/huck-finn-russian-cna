#!/bin/bash
# Run the whole pipeline in order, from the raw texts to the reports:
#   verify raw checksums -> clean -> tag (+ POS check) -> networks (+ length-matched samples; verify network
#   checksums) -> classic measures -> betweenness -> Delta/clustering/embedding -> motifs
# Then check that every report whose numbers do not depend on the mfinder randomization
# ([reproduce].compared_reports in config/pipeline.toml) equals the committed version byte for byte.
#
# Modes:
#   (default) full: the motif step analyses the final R = 1000 mfinder runs (motif_final.py) if they exist in
#             data/interim/mfinder/final/. It does not start them: they take about 1.5 days with the five
#             texts in parallel (scripts/run_mfinder_all.sh 1000; length-matched samples:
#             scripts/run_mfinder_lengthmatched.sh 1000, about 8 hours).
#   --quick   the motif step runs mfinder with R = [reproduce].quick_r (2) on all five texts in parallel, into
#             [reproduce].quick_out_dir/<timestamp>/, writes an R = 2 motif report there (not over the committed
#             one), and checks N_real of every motif against the committed docs/motifs/motif_report.md.
#             A few minutes for mfinder; tagging and betweenness dominate the run time.
#
# Needs: uv sync --frozen; tools/mfinder/build.sh; the Stanza models (uv run --frozen python -m huckfinn_cna.tag
# --download-only); the raw texts in data/raw/ (see README.md); the old repository at [paths].old_repo for the
# old-vs-new comparisons. Exits non-zero at the first failing step or if any compared report differs.
#
# Usage: scripts/reproduce.sh [--quick]
set -euo pipefail

quick=0
case "${1:-}" in
    "") ;;
    --quick) quick=1 ;;
    *) echo "usage: $0 [--quick]" >&2; exit 2 ;;
esac
repo="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo"
py() { uv run --frozen python "$@"; }
step() { echo; echo "=== $(date '+%H:%M:%S') $*"; }

# Line 1: network checksums file; 2: motif report; 3: final runs dir; 4: final R; 5: quick R; 6: quick dir;
# 7: Stanza model dir; then the compared reports, one per line.
config_values=$(py -c '
from huckfinn_cna.config import load_config
c = load_config()
p, r = c["paths"], c["reproduce"]
print(p["network_checksums"], p["motif_report"], p["mfinder_final_dir"], c["mfinder"]["final"]["r"],
      r["quick_r"], r["quick_out_dir"], p["stanza_model_dir"], *r["compared_reports"], sep="\n")
')
{ read -r checksums; read -r motif_report; read -r final_dir; read -r final_r; read -r quick_r; read -r quick_dir
  read -r model_dir; } <<< "$config_values"
compared=()
while read -r f; do compared+=("$f"); done < <(tail -n +8 <<< "$config_values")
texts=() seeds=()
while read -r text seed; do texts+=("$text"); seeds+=("$seed"); done < <(py -c '
from huckfinn_cna.config import load_config
c = load_config()
for t in c["corpus"]["texts"]:
    print(t, c["mfinder"]["final"]["seeds"][t])
')

start=$(date +%s)
echo "reproduce.sh $([ $quick = 1 ] && echo --quick || echo full) start: $(date '+%Y-%m-%d %H:%M:%S %z')  commit $(git rev-parse HEAD 2>/dev/null || echo none)"

step "1. Raw texts: SHA-256 against data/PROVENANCE.md"
py -m huckfinn_cna.reproduce_check raw

step "2. Cleaning"
py -m huckfinn_cna.clean

step "3. Tokenization and tagging (Stanza + spaCy), POS check"
if [ ! -f "$model_dir/resources.json" ]; then
    echo "no Stanza models in $model_dir; run: uv run --frozen python -m huckfinn_cna.tag --download-only" >&2
    exit 2
fi
py -m huckfinn_cna.tag
py -m huckfinn_cna.pos_check

step "4. Networks and length-matched samples; checksums against $checksums"
expected=$(mktemp)
trap 'rm -f "$expected"' EXIT
cp "$checksums" "$expected"           # the committed checksums, before the stages rewrite the file
py -m huckfinn_cna.networks
py -m huckfinn_cna.lengthmatched
shasum -a 256 -c --quiet "$expected"
echo "networks: all $(grep -c '^[0-9a-f]' "$expected") files match"

step "5. Classic stylometric measures"
py -m huckfinn_cna.lexical

step "6. Betweenness centrality"
py -m huckfinn_cna.centrality

step "7. Delta, clustering and embedding"
py -m huckfinn_cna.delta

if [ $quick = 1 ]; then
    out="$quick_dir/$(date +%Y%m%d-%H%M%S)"
    step "8. Motifs (quick): mfinder R = $quick_r, five texts in parallel, into $out"
    mkdir -p "$out"
    pids=()
    for i in "${!texts[@]}"; do
        scripts/run_mfinder.sh "${texts[$i]}" "$quick_r" "${seeds[$i]}" "$out/${texts[$i]}_r${quick_r}" \
            > "$out/${texts[$i]}_r${quick_r}.console.txt" 2>&1 &
        pids+=($!)
    done
    failed=0
    for i in "${!texts[@]}"; do
        if wait "${pids[$i]}"; then echo "${texts[$i]}: exit 0"; else echo "${texts[$i]}: FAILED (see $out/${texts[$i]}_r${quick_r}.console.txt)"; failed=1; fi
    done
    [ $failed = 0 ] || exit 1
    runs=()
    for t in "${texts[@]}"; do runs+=("$out/${t}_r${quick_r}"); done
    py -m huckfinn_cna.motifs "${runs[@]}" --report "$out/motif_report_r${quick_r}.md"
    py -m huckfinn_cna.reproduce_check nreal "${runs[@]}"
else
    step "8. Motifs (full): final R = $final_r runs"
    missing=()
    for t in "${texts[@]}"; do [ -d "$final_dir/${t}_r${final_r}" ] || missing+=("$t"); done
    if [ ${#missing[@]} = 0 ]; then
        py -m huckfinn_cna.motif_final
    else
        echo "No final R = $final_r run for: ${missing[*]} (in $final_dir)."
        echo "These runs take about 1.5 days with all five texts in parallel (the longest, Ru1, about 35 hours),"
        echo "so this script does not start them. Start them with:"
        echo "    caffeinate -ims scripts/run_mfinder_all.sh $final_r      (progress: scripts/mfinder_status.sh $final_r)"
        echo "then run: uv run --frozen python -m huckfinn_cna.motif_final"
        echo "Motif step skipped; the other stages are complete."
    fi
fi

step "Check: reports that do not depend on the randomization, against the committed versions"
differ=0
for f in "${compared[@]}"; do
    if git diff --quiet HEAD -- "$f"; then echo "same:    $f"; else echo "DIFFERS: $f"; differ=1; fi
done
changed_figures=$(git status --porcelain -- docs/amendment/figures | wc -l | tr -d ' ')
echo "figures differing from the committed PNGs (informational; rendering can vary by platform): $changed_figures"
if [ $quick = 0 ] && [ ${#missing[@]} = 0 ]; then
    if git diff --quiet HEAD -- "$motif_report"; then echo "same:    $motif_report"; else echo "DIFFERS: $motif_report"; differ=1; fi
fi

echo
echo "reproduce.sh end: $(date '+%Y-%m-%d %H:%M:%S %z'), $(( ($(date +%s) - start) / 60 )) min"
if [ $differ = 1 ]; then
    echo "FAILED: some reports differ from the committed versions (git diff shows how)" >&2
    exit 1
fi
echo "OK: every compared report equals the committed version"
