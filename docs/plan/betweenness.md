# Plan: betweenness centrality (agreed 2026-10-27)

Items are done in order.

## Item 1: `src/huckfinn_cna/centrality.py`, on the frozen networks

The networks must match the `networks-v1` checksums.

- Primary: exact unweighted betweenness on the undirected version of each network (igraph,
  normalized), no sampling.
- Secondary: directed unweighted betweenness.
- Sensitivity: weighted betweenness with distance = 1 / bigram count (the old method).
- Cross-check igraph against networkx on the smallest network (Ru2) for the unweighted undirected
  case; values must agree to ~1e-9.

## Item 2: frequency adjustment (Amancio 2015)

- For each text, make 30 shuffled versions: shuffle word tokens across the whole text while keeping
  every sentence's length fixed (seeded; seeds derived from a new master seed in config).
- Rebuild each shuffled network with the same rules and compute unweighted undirected betweenness.
- For each word, the adjusted score is its real betweenness divided by its mean shuffled
  betweenness. Also report a z-score against the 30 shuffles.
- Report how strongly raw betweenness correlates with log frequency in each text.

## Item 3: rankings

- For each text: the top 30 words by raw betweenness and by frequency-adjusted betweenness.
- Rank stability across the three variants (Spearman correlation of the top-100 ranks).

## Item 4: old vs new

- Old method: lemmatized whole-text network, crossing sentence boundaries, distance 1/frequency,
  top-20 rank heatmap (Figure 12).
- Old figures quoted in §3.4.2: сказать ranked 69 (Ru1), 61 (Ru2), 15 (Ru4), 13 (Ru5); говорить
  ranked 93 (Ru1) and 15 (Ru5); и, я, в universally top 5. If the old repo has the old betweenness
  CSVs (read-only), use their exact values.
- Show the old rank against the new rank (each variant) for сказать, говорить, и, я and в, and for
  the old Figure 12 word set. State plainly whether the сказать/говорить shift between early and
  later translations survives (a) the new networks, (b) unweighted betweenness and (c) frequency
  adjustment.

## Item 5: outputs

- `docs/centrality/centrality_report.md` (tables only).
- A greyscale replacement for Figure 12 (rank heatmap, same style) using the primary measure, plus a
  frequency-adjusted version, in `docs/amendment/figures/`.

## Item 6: amendment report

Write the "Betweenness Centrality" section. Correct the "unlemmatized" description (the old network
was lemmatized, with 1/frequency distances). Old → new and impact levels, suggested replacement text
for §3.4.2, and update the Executive Summary.

## Wrap-up

Run the tests, confirm the raw and network checksums are unchanged, commit (no push), and summarize,
leading with whether the сказать/говорить finding survives.

## Status (2026-10-27)

All items done. Item 2 changed after a trial on Ru2: ranked by adjusted score, words seen once fill
the top 30, so adjusted ranks use only each text's 200 most frequent lemmas (project owner's decision;
`docs/decisions.md` 42). The old CSVs exist, so old ranks are exact, and the old method re-run on the
old networks reproduces their top-100 ranks.
