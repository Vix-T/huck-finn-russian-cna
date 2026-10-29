# Plan: final motif analysis and length-matched robustness setup (agreed 2026-10-29)

## Part A: analyse the five r = 1000 runs (`data/interim/mfinder/final/Ru*_r1000`)

1. For each text: confirm the exit status and a complete `run.log`; check the input SHA-256 against
   `docs/networks/checksums.txt`; N_real matches igraph; no sentinel values or low-count flags. Then
   compute Z, empirical p-values (both tails), BH q-values within the text, the abundance ratio, the
   concentration, the significance profile (SP) and the standard error of Z. Write `results.tsv` into
   each run folder and a combined table in `docs/motifs/motif_report.md`.
2. Stability: compare Ru1's r = 100 run with its r = 1000 run (Z difference relative to its SE, SP
   correlation). Note that the r = 100 run used the pre-Roman-numeral network (7 edges different).
3. Comparing translations, framed by SP shape, not by counting significant motifs:
   - an SP correlation matrix between the five texts, with average-linkage clustering on
     1 − correlation;
   - the motifs with the largest SP range across texts.
   State plainly which pairings appear, and whether they match the Delta results (Braude–Daruzes
   robust; the Anon–Chukovsky pairing driven by 30% shared wording, which must be caveated wherever
   those two are compared).
4. Old vs new: compare with the old Figure 9 (raw Z) and Figure 10 (SP) values for all five texts
   (`docs/audit/report3_fig9_zscores.csv`; Figure 10 values from the old outputs). Show sign and
   significance flips. Check whether these old claims still hold: "Braude dominates motifs 6 and 46",
   "Engel'gardt indifferent to motif 36", "Daruzes prefers motif 238", "Braude peaks in motif 6 (0.49
   vs Engel'gardt 0.27)", "Anon rivals Braude in motif 12 (0.36)", "Chukovsky and Daruzes share
   similar scores across the key motifs".
5. Figures, greyscale in the old style, in `docs/amendment/figures/`:
   - replacement Fig 9: raw Z heatmap, stars meaning BH q < 0.05 rather than |Z| > 2;
   - replacement Fig 10: SP heatmap, same stars;
   - replacement Fig 11: grouped bars for the three motifs with the largest SP range, with the old
     three key motifs (6, 12, 36) shown as well for continuity, labelled with both mfinder IDs and
     triad codes;
   - the SP correlation heatmap.
6. Amendment report: write the Motif Analysis section (replacing the preliminary §10.1): old → new
   results; why all/most motifs are significant in networks this size and why the SP shape is the
   meaningful comparison; corrected wording for the "95% confidence interval" and the significance
   stars; suggested replacement text for §3.4 / §3.4.1 and the Figure 9–11 captions; an update to the
   §3.5 "agreement with Delta and spectral clustering" sentence based on point 3. Update the
   Executive Summary.

## Part B: length-matched robustness networks

1. For Ru1, Ru3, Ru4 and Ru5, build two contiguous, sentence-aligned samples each, of exactly N word
   tokens (N = Ru2's word-token count): "first" (from the start of the text) and "last" (ending at its
   end). Record the overlap between each pair. Build their networks with exactly the same rules as
   networks-v1 and add their checksums to `docs/networks/checksums.txt` (a separate "length-matched"
   section). Ru2's full network is its own sample.
2. Seeds derived from master seed 20261023 + text + sample, listed in config.
3. Extend the launch and status scripts (or add new ones) to run all 8 samples in parallel at
   r = 1000, writing to `data/interim/mfinder/lengthmatched/TEXT_SAMPLE_r1000/`.
4. Dress rehearsal at r = 2 for all 8 in parallel: check N_real against igraph, and estimate the
   r = 1000 finish time when launched tonight.
5. Run the tests, commit, tag the commit `networks-lm-v1` (no push). Give the detached caffeinate
   launch command and the status command.

Summary: Part A first, leading with whether the SP pattern holds, which pairings appear and which old
claims survive; then Part B: sample sizes, overlaps and estimated run time.

## Status (2026-10-29)

Part A done: all five r = 1000 runs pass the checks; results in `docs/motifs/motif_report.md` and amendment
Section 10. A robustness check of the SP clustering across similarity measures (Spearman, Euclidean) was
added because the finer pairings changed with the measure (decision 56).
