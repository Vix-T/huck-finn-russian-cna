# Complex network analysis of Russian translations of Huckleberry Finn

A reproducible rebuild of a study comparing five Russian translations of Mark Twain's *Adventures of
Huckleberry Finn*. It uses classic stylometry (lexical richness, sentence length, Burrows's Delta,
clustering and embeddings) and complex network analysis of word-adjacency networks (3-node motifs and
betweenness centrality).

The study was first reported in *Final Report 3* (11-2025). This repository replaces that analysis
with:
- a pinned environment;
- one configuration file for every parameter (`config/pipeline.toml`);
- seeded randomness;
- tests;
- one command that runs everything (`scripts/reproduce.sh`).

Where to read more:
- **Methods:** `METHODS.md` describes the methods as implemented.
- **Decisions:** `docs/decisions.md` records every methods decision.
- **Changes from the article:** `docs/amendment/amendment_report.md` gives what changed, why, and
  suggested replacement text for the article.

## Requirements

- macOS or Linux.
- [uv](https://docs.astral.sh/uv/), which installs Python 3.12 and every dependency from `uv.lock`.
- A C compiler for mfinder: the Xcode Command Line Tools on macOS (`xcode-select --install`), or `cc`/`gcc`
  plus `patch` on Linux.
- About 2 GB of disk for the environment and models.
- Network access once, during setup: PyPI, GitHub (the spaCy model wheel) and Hugging Face (the Stanza
  models).

## Setup

```bash
git clone <this repository> huck-finn-russian-cna && cd huck-finn-russian-cna
uv sync --frozen                                  # Python 3.12 + locked dependencies, incl. spaCy ru_core_news_lg 3.8.0
tools/mfinder/build.sh                            # builds tools/mfinder/bin/mfinder from the vendored mfinder 1.2 source with seed.patch
uv run --frozen python -m huckfinn_cna.tag --download   # Stanza 1.15.0 Russian models into models/stanza/
uv run --frozen python -m unittest discover tests        # optional: the test suite
```

**mfinder.** `tools/mfinder/src` is the original mfinder 1.2 source (Kashtan, Itzkovitz, Milo & Alon). The
only change is `tools/mfinder/seed.patch`, which makes it read its random seed from `MFINDER_SEED` instead
of the clock. Without the patch, runs cannot be reproduced. Provenance and verification are in
`tools/mfinder/README.md`.

**spaCy model.** `ru_core_news_lg` 3.8.0 is a pinned direct-URL dependency in `pyproject.toml`, so
`uv sync --frozen` installs it.

## Data

The raw texts are not redistributed. Obtain the five translations below and place them in `data/raw/`
under these exact names, as UTF-8 text. Every file must match its SHA-256 checksum, because every count in
the reports depends on the exact bytes. `scripts/reproduce.sh` checks the checksums first.

| File | Translation | Notes | SHA-256 |
|---|---|---|---|
| `data/raw/Ru1.txt` | Ru-1911-Engelgardt | full | `72617c0a287bf24cbc9cf0831530def83ba4df30e3f164a2653a87168bf6c987` |
| `data/raw/Ru2.txt` | Ru-1926-Anon | abridged | `034bfca71fc719a662af3a1633fdee1ff8442ffca30dadd9fbaf1b1940fe0835` |
| `data/raw/Ru3.txt` | Ru-1933-Chukovsky-(Ed.) | abridged | `8b99a1e09223f9f7f507fc1b08f6769f2c46497abaa6a1a3a898e46b2dc4aad3` |
| `data/raw/Ru4.txt` | Ru-1949-Braude | full | `6d9507d5088497ae661c1e667aff5d91bb4764a3a9a146f73db0ae71252c3805` |
| `data/raw/Ru5.txt` | Ru-1960-Daruzes | full | `898ee91e85619c772084478d6d8c6585433710dbe43a33be18795fb057c9a3d8` |

`data/PROVENANCE.md` is the authoritative list of checksums and records the edition, publisher and digitization source of each file (to be completed by the project owner). The
1933 text is largely a revision of the 1926 one: it shares 30% of the 1926 text's eight-word sequences.
Both are kept and the overlap is stated wherever they are compared (`METHODS.md`, Corpus).

## Reproducing the results

```bash
scripts/reproduce.sh --quick     # whole pipeline; mfinder with R = 2; checks against the committed reports
scripts/reproduce.sh             # whole pipeline; motif step uses the final R = 1000 runs if present
```

**Pipeline order.** The script runs these stages in order:
1. Verify the raw-text checksums.
2. Cleaning.
3. Tagging (Stanza + spaCy) and the POS check.
4. Networks and length-matched samples, with the network checksums verified against
   `docs/networks/checksums.txt`.
5. Classic measures.
6. Betweenness.
7. Delta, clustering and embedding.
8. Motifs.

Each stage is also a module you can run on its own (`uv run --frozen python -m huckfinn_cna.<stage>`; see
each module's docstring).

**Quick mode.** After running, `--quick` checks that every report whose numbers do not depend on the mfinder
randomization equals the committed version byte for byte (`[reproduce].compared_reports`). It also checks
that the real-network motif counts (N_real) of the R = 2 runs equal those in the committed motif report.
The R = 2 runs and their report go to `outputs/reproduce_quick/<timestamp>/`. Expect roughly an hour, most
of it tagging and the betweenness shuffles.

**The R = 1000 motif runs** take about 1.5 days with the five texts in parallel (Ru1, the longest, about
35 hours). They are started separately:

```bash
caffeinate -ims scripts/run_mfinder_all.sh 1000            # five texts in parallel (macOS; drop caffeinate on Linux)
scripts/mfinder_status.sh 1000                              # progress and estimated finish
uv run --frozen python -m huckfinn_cna.motif_final          # analysis, once all five have finished
scripts/run_mfinder_lengthmatched.sh 1000                   # length-matched robustness samples, about 8 hours
```

Seeds come from `config/pipeline.toml`, so a rerun reproduces the committed runs exactly.

**Old-versus-new comparisons.** The centrality and Delta stages recompute the old values from the old
repository (`[paths].old_repo`, tag `report-v3`). Those two stages need that folder.

## Outputs

- `docs/amendment/amendment_report.md`: the amendment report, with an Executive Summary and a table of
  changes per stage. Figures are in `docs/amendment/figures/`.
- `docs/<stage>/*_report.md`: one tables-only report per stage (cleaning, tagging, networks, classic,
  centrality, delta, motifs).
- `docs/networks/checksums.txt`: SHA-256 of every network file (frozen inputs to the motif and centrality
  stages).
- `data/interim/`: intermediate files (cleaned and tagged texts, networks, mfinder runs, result tables).
  Not committed; regenerated by the pipeline.
- `docs/plan/`: the plan of each stage. `docs/audit/`: the audit of the old data and code.

## Versions and tags

| Tag | Repository | What it marks |
|---|---|---|
| `report-v3` | old repository (`nlp-descriptive-russian-translations`) | the code behind *Final Report 3* |
| `networks-v1` | this repository | the frozen networks used by the final R = 1000 motif runs and by betweenness |
| `networks-lm-v1` | this repository | adds the eight length-matched sample networks, seeds and launcher |

Environment: Python 3.12 with the versions locked in `uv.lock`. Key pins: Stanza 1.15.0, spaCy 3.8.16,
`ru_core_news_lg` 3.8.0, mfinder 1.2 (seed-patched).
