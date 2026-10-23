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
