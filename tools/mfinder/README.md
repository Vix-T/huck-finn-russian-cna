# mfinder 1.2 (vendored)

mfinder is the network-motif tool of Kashtan, Itzkovitz, Milo and Alon (Weizmann Institute,
version 1.2, May 2005). It is used here as a cross-check of the Python motif counts.

## Provenance

- `src/` is the **original mfinder 1.2 C source exactly as first imported into the pymfinder
  repository**: https://github.com/stoufferlab/pymfinder, commit
  `62acf9716b45d815140ac8ae507b6c8d7986f535` (2010-12-23, "Initial import of the mfinder wrapper
  code"), directory `mfinder1.2/`. Every file is byte-identical to that commit. Fetched 2026-10-21
  (repository HEAD then: `e1791deb775d8dffbeaad6867b180d2bb042e9bd`).
- `LICENSE` is the pymfinder repository's MIT license (HEAD). The pymfinder README states that
  the mfinder code is included with the consent of its author, Nadav Kashtan.
- `doc/mfinder-manual.pdf` (mfinder Tool Guide 1.2) and `doc/mfinder-motif-dictionary.pdf` come
  from `pymfinder/mfinder/` at HEAD.

### Why not the copy in pymfinder/mfinder/ at HEAD

That copy was later modified for use as a Python extension, and the modifications matter for a
command-line run:

1. `gnrl_init()` has every output-file `fopen` commented out ("removed because we don't want any
   output files"), so a command-line build crashes on its first write.
2. `switches.c` wraps the whole edge-switching randomization in `for(xxx=0;xxx<10;xxx++)`, so each
   random network receives about 10 times the switches documented in the manual (§3.2: 100–200 ×
   the number of edges). That changes the random-number sequence and the run time.
3. Smaller changes: `main.c` split into `globals.h`/`globals.c`; `INFINITY` renamed; self-loop
   bookkeeping; motif sizes ≥ 5 listed differently in `results.c`.

The original import has none of these, needs no change except the seed patch, and reproduces the
manual's worked example exactly.

## Local change: seed.patch

mfinder seeds its random-number generator (`ran2`, Numerical Recipes) from the clock in
`init_random_seed()` (`random.c`). `seed.patch` makes it read the seed from the environment
variable `MFINDER_SEED` and exit with status 2 if the variable is unset or not an integer in
1..2147483646. The seed is stored negated because `ran2` runs its initialisation only for a
seed ≤ 0. (The original `time(NULL) | 0x80000000` is meant to be negative, but where `long` is
64 bits, as on this Mac, it is positive, so the original never ran that initialisation.)

## Build

    tools/mfinder/build.sh

copies `src/` to `build/`, applies `seed.patch`, and compiles `bin/mfinder` with the original
Makefile's flags (`-O3 -DUNIX=1`). `build/` and `bin/` are gitignored. Built 2026-10-21 on macOS
26 (Darwin 25.6.0, arm64), Apple clang 21.0.0: binary SHA-256
`c0734777acaaa9e4badb1aeb8bac080edbfc3b2c3b467efb212eab4a051a8bb0`.

## Verification (2026-10-21)

1. Manual example network (`tests/data/mfinder_manual_example.txt`, manual Chapter 4): NREAL equals
   the manual's counts and the igraph/networkx counts (`tests/test_mfinder.py`).
2. Ru1, `-r 2`, seed 20261021, run twice: `_MAT_R.txt` byte-identical and `_OUT.txt` identical
   apart from the runtime lines.
3. Ru1 NREAL equals the igraph/networkx counts for all 13 triads
   (`uv run python -m huckfinn_cna.networks --check-mfinder INPUT OUT`).

Ru1 timing on this Mac (Apple Silicon): real network 1.20 minutes, 1.81–1.83 minutes per random
network.

Five texts in parallel (`scripts/run_mfinder_all.sh 2`, 2026-10-22), per random network: Ru1 2.12 min,
Ru2 24.5 s, Ru3 37 s, Ru4 1.85 min, Ru5 1.48 min; real network 15 s (Ru2) to 1.45 min (Ru1).

## Use

`scripts/run_mfinder.sh TEXT R SEED [OUT_DIR]` (flags from `config/pipeline.toml`, `[mfinder]`).
The final run is `scripts/run_mfinder_all.sh R` (all five texts in parallel, seeds from
`[mfinder.final]`); progress: `scripts/mfinder_status.sh R`. The Ru1 runs above predate `-oi`. Flags used,
as described in the manual (§2.3, §2.5):

| Flag | Meaning |
|---|---|
| `-s 3` | subgraph (motif) size |
| `-r N` | number of random networks |
| `-f NAME` | output prefix; writes `NAME_OUT.txt` |
| `-q` | quiet: nothing printed to the screen |
| `-orall` | writes `NAME_MAT_R.txt`: the count of each subgraph in every random network (one row per subgraph ID, ascending; one column per random network; no ID column) |
| `-oi` | writes `NAME_INTER.txt`: the full results after every random network, flushed each time. Used only for progress (`scripts/mfinder_status.sh`); for a fixed seed `_MAT_R` and `_OUT` are unchanged (`tests/test_mfinder.py`). About 1.35 KB per random network for these networks |

Not used: `-omem` (member lists, partial samples of at most `-maxmem`, default 1000), and `-nu`
(see below).

**`-nu`** ("Don't count uniqueness and ignore uniqueness threshold"). Uniqueness is the number of
occurrences of a subgraph in the real network with completely disjoint node sets (a lower bound;
manual §3.3). It fills the UNIQ column and is one of mfinder's criteria for listing a subgraph as a
motif (default U ≥ 4). It is computed for the real network only and does not affect NREAL, the
random-network counts, Z-scores or p-values. `-nu` switches the computation off and sets the
threshold to 0.
