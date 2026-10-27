# Plan: Delta, clustering and embedding (agreed 2026-10-27)

Items are done in order.

## Item 1: features (`src/huckfinn_cna/delta.py`)

- From `data/interim/tagged/Ru#.tsv`: word tokens, lowercased lemmas with ё → е. Two views: **all**
  (stop words kept; primary) and **content** (UPOS content set).
- Units: (a) contiguous, paragraph-aligned 5,000-word blocks from every text (drop the remainder;
  list block counts per text), and (b) whole texts.
- Relative frequencies (count / total word tokens in the unit). Rank MFW by the mean of per-unit
  relative frequencies (stylo convention), computed over blocks.
- Grid: N = 100, 200, …, 1000 MFW; culling 0% and 100% (100% = words present in all five texts).
- z-scores: column means and SDs (ddof = 1) computed across all blocks of all texts, then applied to
  blocks and to whole texts alike.

## Item 2: distances

- Cosine Delta (primary) = cosine distance between z-score vectors; Classic Delta = cityblock / N.
- Text-level distance matrices (whole texts, using the block-based z-scores) for every grid setting.
- Do not use faststylometry (its default token pattern drops all Cyrillic). Plain numpy/scipy only.

## Item 3: clustering

- scipy linkage on the condensed distance vector (squareform), method "average" (primary), with
  "complete" as a check. Ward only on Euclidean distance between L2-normalized z-score vectors, as a
  sensitivity check.
- Always show the distance matrix (heatmap) next to any dendrogram.
- Robustness:
  - a majority-rule (≥ 0.5) consensus tree across the full grid × {Cosine, Classic};
  - an Eder-style consensus network (nearest neighbour weight 1, two runners-up weight 0.5 each),
    summed over the grid.
- Block level:
  - within-translation coherence: the share of blocks whose nearest neighbour comes from the same
    translation, plus the silhouette score;
  - a translator × translator matrix of nearest other-translation neighbours.
  State plainly whether blocks group by translation before making any between-translation claim.

## Item 4: embeddings

- PCA on standardized block-level relative frequencies (N = 100, 300, 1000): explained variance for
  PC1–4 and the top ± loadings. Flag any component that mainly separates abridged from full texts.
- Classical MDS on the text-level Cosine Delta matrix (N = 300, culling 0%) as the 2-D map for the
  main text.
- Spectral embedding (appendix only): the same z-scored MFW features, cosine affinity with negatives
  clipped, sklearn `spectral_embedding` with `norm_laplacian=True` and a fixed `random_state`. Report
  λ2 and λ3, and say whether the layout is unique. Describe it accurately (symmetric normalized
  Laplacian, first eigenvector dropped, rescaled by D^−1/2), not as L = D − W.

## Item 5: old vs new

- Old: raw counts over the full vocabulary (not Burrows's Delta); z-scores across 5 documents; Ward
  on non-Euclidean distances; the cosine dendrogram passed a square matrix to linkage (so it
  clustered rows as feature vectors); spectral embedding used TF-IDF whose default tokenizer drops
  one-letter words (и, я, в, с, к, а); old Figure 7 = cosine similarity heatmap with stop words;
  Figure 8 = spectral embedding.
- If old result CSVs exist in the old repo (read-only), e.g. `delta_results_*.csv` or similarity
  matrices, compare exact values. Otherwise compare against the Figure 7/8 descriptions in Final
  Report 3.
- State plainly which pairings/clusters from the old figures survive (e.g. Braude–Daruzes, the
  abridged pair, Engel'gardt as outlier), and whether the §3.5 claim of agreement between the CNA
  results and the Delta/spectral clustering still holds (motif results pending: judge only the Delta
  side for now).

## Item 6: outputs

- `docs/delta/delta_report.md` (tables only).
- Greyscale figures in the old style, in `docs/amendment/figures/`: replacement Fig 7 (distance
  heatmap, primary setting), replacement Fig 8 (classical MDS), consensus tree, consensus network,
  the spectral plot for the appendix, and a replacement Fig 13 (appendix MFW rank heatmap: top 30
  content words, ranks per text).

## Item 7: amendment report

Write the "Delta, Clustering and Embedding" section (old → new, reasons, impact levels, suggested
replacement text for §3.3 and the relevant §3.5 sentences), and update the Executive Summary.

## Wrap-up

Run the tests, confirm the raw checksums are unchanged, commit (no push), and summarize, leading
with: do blocks group by translation, which pairings survive, and is spectral embedding stable
enough to keep even in the appendix.

## Status (2026-10-27)

All items done. Before this stage the old repository folder was replaced by the old local working
folder; its inventory and re-verification are in `docs/audit/data_audit.md`, and the classic-measure
old values were updated to the exact CSV values. No old Delta or similarity CSV exists, but Figures 7
and 8 and the old Delta recompute exactly from the old DTMs and lemmatized texts. Two diagnostics were
added to answer item 3's "do blocks group by translation": story-position gap and shared n-grams
(decision 49). They show that blocks group by story position (Engel'gardt excepted) and that
Ru-1926 and Ru-1933 share 30% of their eight-word sequences.

**Follow-up (2026-10-27): RBF spectral embedding.** Before dropping spectral embedding, a properly
constructed version was tested (Gaussian affinity on Cosine Delta, σ = 0.5/1/2 × median, texts and
blocks). It is connected and stable but repeats MDS (texts) and PCA (blocks); it stays in the appendix
(decision 53, amendment Section 8).
