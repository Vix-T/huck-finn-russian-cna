# Plan: motif runs (agreed 2026-10-22)

Items are done in order; item 4 is the final all-texts mfinder run.

## Item 1: Ru1 r=100 analysis

Write `src/huckfinn_cna/motifs.py`, which parses mfinder output plus the `-orall` random counts
(`_MAT_R`). For each of the 13 triads (mfinder ID + triad code), compute:

- N_real; the null mean and SD; Z;
- one-sided empirical p-values, (b+1)/(r+1), for over- and under-representation;
- Benjamini–Hochberg q-values across the 13 triads;
- Milo's abundance ratio, (N_real − mean)/(N_real + mean + 4); the concentration; the significance
  profile, SP = Z/‖Z‖;
- the approximate standard error of each Z, so we know how much Z could move between runs.

Flag triads with N_real < 5 or a null mean < 5, and any sentinel value (888888). Check that N_real
matches igraph. Write `results.tsv` in the run folder and a summary in `docs/motifs/motif_report.md`.

Old vs new: compare Ru1's new Z and SP with the Figure 9 run (`docs/audit/report3_fig9_zscores.csv`,
Ru1 row). Show old, new and the difference for each motif, and note any sign or significance flips.
Note that the old network crossed sentence boundaries and used the old cleaning. Append this to the
Motif Analysis section of the amendment report, marked preliminary (one text, r=100).

## Item 2: seeding sentence

In the amendment report's motif-audit section, add: the original mfinder 1.2 seeds its random
generator from the clock in a way that, on 64-bit machines, never runs the generator's setup step,
so the 2025 runs' randomization can't be reproduced; the rebuilt pipeline patches mfinder to take an
explicit, recorded seed (`tools/mfinder/seed.patch`).

## Item 3: Roman numerals

Treat Roman-numeral tokens (e.g. XVI, III in «Людовика XVI») like Arabic digits: `is_word = 0`, so
the network skips them as it already does numbers. Rule: numerals written as numerals aren't words;
spelled-out numbers are. Make this a single function used by tag/networks, with tests (XVI, III, IV
skipped; Cyrillic «И» and ordinary words unaffected). Rebuild the networks. Write
`docs/networks/checksums.txt` with the sha256 of every `*_mfinder.txt`, and note that the Ru1 r=100
preview ran on the pre-fix network (give the number of edges that changed). Record the rule in
`docs/decisions.md`.

## Item 4: Friday run (all five texts in parallel, r=1000, launch Friday 19:30)

- Seeds: numpy `SeedSequence([20261023, text_index])`, mapped into mfinder's range 1..2147483646.
  Master seed 20261023 (separate from the bootstrap seed). List the per-text seeds in config and
  `docs/decisions.md`.
- `scripts/run_mfinder_all.sh R` launches Ru1–Ru5 in parallel through `run_mfinder.sh`, with output
  in `data/interim/mfinder/final/TEXT_r{R}/`. `scripts/mfinder_status.sh` shows each text's random
  networks done out of R, elapsed time, and estimated finish.
- Dress rehearsal: actually run `scripts/run_mfinder_all.sh 2`, then `motifs.py` on all five
  outputs. Confirm N_real matches igraph for every text. From the timings under parallel load,
  estimate each text's finish time for r=1000 starting Friday 19:30.
- Check free disk space against the expected `-orall` output size.
- Run the tests, commit, then tag `networks-v1` (don't push).
- Give the exact caffeinate Terminal command for Friday and the status command.

## Status (2026-10-22)

Items 1–3 done, and item 4 up to the launch (commit `1418d93`, plus docs; tag `networks-v1`).

**Dress rehearsal** (`scripts/run_mfinder_all.sh 2`, 2026-10-22 20:33–20:39, five texts in parallel):
all five exited 0; `motifs.py` on all five outputs: N_real equals igraph/networkx for all 13 triads in
every text, recomputed Z equals mfinder's Z, no flags. Output per text at R = 2: `_INTER.txt` about
1.35 KB per random network, `_MAT_R.txt` about 13 × 8 bytes per random network, so R = 1000 needs
about 1.5 MB per text (free disk: 820 GiB).

**Estimated finish for R = 1000 from Fri 23 Oct 19:30**, from mfinder's per-network times under
five-way parallel load (rounded, 2 random networks each, so rough):

| Text | Real network | Per random network | Total | Estimated finish |
|---|---|---|---|---|
| Ru2 | 15 s | 24.5 s | 6.8 h | Sat 24 Oct 02:20 |
| Ru3 | 26 s | 37 s | 10.3 h | Sat 24 Oct 05:45 |
| Ru5 | 59 s | 1.48 min | 24.7 h | Sat 24 Oct 20:10 |
| Ru4 | 1.15 min | 1.85 min | 30.9 h | Sun 25 Oct 02:20 |
| Ru1 | 1.45 min | 2.12 min | 35.4 h | Sun 25 Oct 06:50 |

Ru1 alone ran at 1.92 min per random network (r = 100 preview), so Ru1 and Ru4 should speed up as
the smaller texts finish; Ru1's finish is likely between Sun 03:30 and 07:00.
