# Plan: classic stylometric measures (agreed 2026-10-26)

Items are done in order.

## Item 0: quick answer from the POS bootstrap

From the POS bootstrap already computed (`docs/tagging/tagging_report.md`): is Ru-1960 Daruzes's
NOUN : VERB ratio distinguishable from Ru-1949 Braude's (non-overlapping 95% CIs, both taggers)? Does
the 1911→1960 verb-form trend hold outside the CIs? Make sure the amendment report's tagging section
states both plainly.

## Item 1: inputs and token definition

- Read `data/interim/tagged/Ru#.tsv`. Word tokens = `is_word = 1`. Use lowercased lemmas with ё → е
  (the same rule as network nodes).
- Two views: **all** (stop words kept; the primary view) and **content** (UPOS in the config content
  set), the latter for comparison with the article's current numbers.

## Item 2: measures (`src/huckfinn_cna/lexical.py`)

- MSTTR: chunked TTR over 2,000-token chunks, with the old code's rule for the final partial chunk
  (drop it if shorter than half a chunk).
- MATTR: windows 100, 500 and 1,000 (500 is primary).
- MTLD: bidirectional, threshold 0.72 (McCarthy & Jarvis 2010).
- HD-D: sample size 42.
- Yule's K: 10^4 · (Σ i² · V_i − N) / N², as in the old code.
- Honoré's R: 100 · ln N / (1 − V1/V). The old code computed this under the name "Honoré's W";
  label it R everywhere.
- Unigram Shannon entropy, log2.

Cross-check MTLD, HD-D and MATTR against the `lexicalrichness` package (dev dependency, from PyPI) on
all five texts and report any disagreement. Unit tests with known values on toy inputs.

## Item 3: length control

- Report every measure (a) on whole texts and (b) on equal-length samples: contiguous,
  paragraph-aligned samples of N word tokens, N = the shortest text's word count rounded down to the
  nearest 1,000.
- For each full text, take all non-overlapping samples, plus 200 random-start contiguous windows
  (seeded) to show the spread. Ru2 contributes its single whole-text sample.
- State plainly, for each measure, whether the rank order of the five texts is the same at equal
  length as on whole texts.

## Item 4: sentence length (Stanza's sentences in the tagged files)

- Words per sentence, counting word tokens only (punctuation excluded): mean, median, IQR.
- Punctuation marks per 1,000 words, as a separate measure.
- Dialogue is taken as the splitter gives it.

## Item 5: uncertainty

Paragraph-level bootstrap, 1,000 resamples, for the whole-text measures and sentence length. Seed
derived from a new master seed in config, recorded.

## Item 6: old vs new

- Look first in the old repo (read-only) for `stylometric_summary.csv` and
  `lexical_diversity_results.csv` and use their exact values if present; otherwise the values printed
  in Final Report 3 (Yule's K: Engel'gardt 13.3, Braude 24.9, Daruzes 23.2; Honoré "W": Ru2 2060.3,
  Ru3 1962.9; chunked TTR 0.55, 0.52, 0.519, ~0.498, ~0.485; average sentence length Ru1 18.6,
  Ru2 ~17, Ru3 ~15, Ru4/Ru5 ~19–20; unigram entropy Ru1 9.949, Ru4/Ru5 ~9.3). Note which are exact and
  which approximate.
- Old-vs-new table per measure. Note that the old values used stop-word-removed lemmas (TTR, K, R),
  the old cleaning (ё deleted, hyphenated words fused, OCR splits), and punctuation counted as words
  (sentence length).

## Item 7: outputs

- `docs/classic/classic_report.md`: tables only, no text excerpts.
- Greyscale replacements for Figures 1–5 in the old figures' style (labelled bars; Yule's K labelled
  "lower = richer"), in `docs/amendment/figures/`.

## Item 8: amendment report

Write the "Classic Stylometric Measures" section: for each measure, old → new, the reason and the
impact level. State plainly (a) whether Engel'gardt's richness lead holds under MATTR/MTLD and at equal
length; (b) whether the Honoré contradiction with Yule's K persists once length is controlled; (c) how
the sentence-length values and ranking change once punctuation isn't counted. Suggested replacement
text for §3.1 and §3.2.1–3.2.2; update the Executive Summary table.

## Item 9: wrap-up

Update `docs/decisions.md`. Run the tests, confirm the raw checksums are unchanged, commit (no push),
then summarize, leading with (a), (b) and (c).

## Status (2026-10-26)

All items done. Item 0: the tagging section already stated both answers plainly (Daruzes and Braude
not distinguishable on NOUN : VERB under either tagger; the 1911→1960 verb-form trend holds outside
the intervals), so it was left unchanged. Item 5 changed after the first run: the paragraph
bootstrap is biased for MSTTR, MATTR, MTLD, Honoré's R and entropy, so intervals are reported only
for sentence length, Yule's K and HD-D, and the other five use the spread of the equal-length random
windows (project owner's decision; `docs/decisions.md` 35).
