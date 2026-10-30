# Methods

This file describes the methods as they are implemented in this repository, stage by stage, in the order the
pipeline runs them (`scripts/reproduce.sh`). It is written for a reviewer and is the reference for the
methods. Every parameter named here lives in `config/pipeline.toml` (cited as `[section].key`). Every choice
that needed a decision is recorded, with its reason, in `docs/decisions.md` (cited as "D" plus its number).
How the results differ from *Final Report 3*, and why, is in `docs/amendment/amendment_report.md` (the
amendment), whose section numbers are given below.

## Corpus

Five Russian translations of *Adventures of Huckleberry Finn* (`[corpus]`). The raw texts are not
redistributed: see `README.md` and `data/PROVENANCE.md` for the expected files and their SHA-256 checksums.

| File | Label | Notes |
|---|---|---|
| `Ru1.txt` | Ru-1911-Engelgardt | full |
| `Ru2.txt` | Ru-1926-Anon | abridged |
| `Ru3.txt` | Ru-1933-Chukovsky-(Ed.) | abridged |
| `Ru4.txt` | Ru-1949-Braude | full |
| `Ru5.txt` | Ru-1960-Daruzes | full |

**The two abridged texts are not independent.** Ru-1933 shares 30% of Ru-1926's distinct eight-word
sequences (23% of its own), against at most 0.7% for any other pair, so the 1933 edition is largely a
revision of the 1926 text. Both are kept as two published editions, and this is stated wherever they are
compared (D61; the n-gram diagnostic is D49, `[delta].overlap_ngrams`).

## 1. Input handling

- Raw files in `data/raw/` are only read, never modified. Their SHA-256 values must match
  `data/PROVENANCE.md` (`huckfinn_cna.reproduce_check raw`).
- Encoding `utf-8-sig`, which strips the byte-order mark (`[clean].encoding`).
- CRLF and lone CR become `\n` in memory, and text is normalized to Unicode NFC (`[clean].unicode_form`).
- A paragraph is one non-empty line. No text is ever joined across lines (D I1–I3).

Amendment §4.1.

## 2. Cleaning (`src/huckfinn_cna/clean.py`)

Raw text becomes `data/interim/clean/Ru#.txt`. Rules a–k run in order, each parameter in `[clean.*]`. Every
change is logged per text (`Ru#_changes.tsv`), and the run fails if a rule's preconditions are violated.

| Rule | What it does | Config | Decisions |
|---|---|---|---|
| a | Encoding, line endings, NFC (above) | `[clean]` | I1–I3 |
| b | Removes Twain's Notice (Ru1, Ru5) and Explanatory note (Ru5), matched on text, each found exactly once. Other short title or "Конец"-type lines are only proposed in the report. | `[[clean.front_matter]]`, `[clean.front_back_proposals]` | 7 |
| c | Removes bracketed editorial notes containing «Прим.»; other bracketed segments are kept and listed | `[clean.editorial_notes]` | 6 |
| d, g | Whitespace collapsed and stripped. «--» and spaced dashes become « — ». All quote styles become « ». Three or more full stops become «…». Doubled commas collapsed. | `[clean.punctuation]` | 5 |
| e | Latin look-alike letters become Cyrillic, inside tokens with at least one Cyrillic letter | `[clean.latin_lookalikes]` | 4 |
| f | Standalone «II» becomes «И» | `[clean.standalone_tokens]` | 4 |
| h | Broken hyphenation rejoined only for the particles -то, -либо, -нибудь, -ка, -таки; for по- + -ому/-ему/-ски/-цки/-ьи; for кое- + any word; and for из-под, из-за | `[clean.hyphenation]` | 3, 10 |
| i | OCR split words joined when pymorphy3 knows AB and does not know A or B. Never after не/ни, never before a capital. A seeded sample of joins is reviewed by hand. | `[clean.split_words]` | 2, 9 |
| i2 | A B joined when AB occurs at least twice as one token in the same text, «A B» occurs in no other text, and neither part is a single letter | `[clean.attested_joins]` | 12, 13 |
| j | ё becomes е | `[clean.yo]` | 1 |
| k | Manual corrections (`data/corrections/manual.tsv`), each matching exactly once, applied last | `[paths].manual_corrections` | 11 |

Report: `docs/cleaning/cleaning_report.md`. Amendment §4.

## 3. Tokenization and tagging (`src/huckfinn_cna/tag.py`, `pos_check.py`)

- **Pinned taggers.** Stanza 1.15.0, Russian resources 1.15.0, packages `[tag].stanza_packages` (UD
  SynTagRus); spaCy 3.8.16 with `ru_core_news_lg` 3.8.0. The run refuses to start if the installed
  versions differ from the config. Inference has no randomness (D14).
- **Tokens and sentences.** Stanza tokenizes and splits sentences one paragraph at a time, so no sentence
  crosses a paragraph. Hyphenated words that Stanza split are merged back into one token, then tagged and
  lemmatized as pre-tokenized input (D15). spaCy tags exactly the same tokens, as a cross-check (D16).
- **Integrity checks.** The run fails if any character is lost or added, or if a hyphenated word is split
  (D17).
- **Columns.**
  - `is_word`: letters only, internal hyphens allowed, and not a Roman numeral (D18, D27).
  - `is_punct`: every character is punctuation.
  - `is_content`: UPOS in `[tag].content_upos` (NOUN, VERB, ADJ, ADV, PROPN).
- **POS re-check.** UPOS proportions, ranks, NOUN : VERB and VerbForm shares under both taggers, with a
  paragraph bootstrap (1,000 resamples, percentile 95%, seed `[pos_check].bootstrap_seed`). Values are
  called distinguishable when their intervals do not overlap (D20, D22).

Output: `data/interim/tagged/Ru#.tsv`. Report: `docs/tagging/tagging_report.md`. Amendment §6.

## 4. Word-adjacency networks (`src/huckfinn_cna/networks.py`, `lengthmatched.py`)

- **Nodes.** Stanza lemmas of word tokens, lowercased, mapped by `[networks].lemma_char_map` (ё → е,
  D23). Every word lemma is a node.
- **Edges.** Within a sentence, each word links to the next word, skipping tokens that are not words
  (punctuation, numbers, Roman numerals). No edge crosses a sentence boundary. Self-loops are dropped and
  counted. Bigram counts are kept as weights for weighted betweenness; the motif graph is unweighted
  (D8, D24).
- **Triad counts.** The 13 connected 3-node triads are counted exactly with igraph and with networkx, and
  the run fails unless the two agree. Classes are labelled with mfinder subgraph IDs
  (`[[networks.triads]]`, D25).
- **Frozen networks.** The SHA-256 of every `Ru#_mfinder.txt` is in `docs/networks/checksums.txt`. Later
  stages and the mfinder launchers refuse to run if the networks differ (tag `networks-v1`).
- **Length-matched samples** (`[lengthmatched]`, D58).
  - For Ru1, Ru3, Ru4 and Ru5, two contiguous samples of exactly N = 48,182 word tokens (Ru2's length):
    - "first": whole sentences from the start of the text;
    - "last": whole sentences back from the end.
    - Only the innermost sentence of each sample is cut.
  - Networks are built with the same rules, and their checksums are in a separate section of the
    checksums file (tag `networks-lm-v1`).
  - Overlap between each text's two samples: Ru1 0, Ru3 69%, Ru4 9.5%, Ru5 8.8% of N.

Reports: `docs/networks/network_report.md`, `lengthmatched_report.md`. Amendment §9, §10.5.

## 5. Motif statistics (`tools/mfinder`, `scripts/run_mfinder*.sh`, `src/huckfinn_cna/motifs.py`, `motif_final.py`)

- **mfinder.** mfinder 1.2, vendored unmodified from the original pymfinder import (D26). The only change
  is `tools/mfinder/seed.patch`, which reads the random seed from `MFINDER_SEED` instead of the clock.
  Without it, the 2025 runs cannot be reproduced (amendment §5.2).
  - Flags: `-s 3 -r R -f NAME` plus `[mfinder].extra_flags` (`-q -orall -oi`). `-orall` keeps every
    random network's counts; `-oi` changes no result.
  - Null model: mfinder's degree-preserving edge switching.
- **Runs.** R = 1000 random networks per text, all five texts in parallel (`[mfinder.final]`, D29–D30).
  - Seeds: `SeedSequence([20261023, text index])` mapped into 1..2147483646.
  - Length-matched samples: `SeedSequence([20261023, text index, sample number])` (`[mfinder.lengthmatched]`,
    D59).
- **Statistics** (`[motifs]`, D28), computed from the per-random-network counts, not mfinder's summary:
  - null mean and SD (ddof 1); Z = (N_real − mean)/SD, checked against mfinder's Z;
  - empirical one-sided p = (b + 1)/(R + 1), with Benjamini–Hochberg q over the 13 triads in each
    direction; a triad is **significant** when q < 0.05 in the direction of its Z (D56);
  - abundance ratio (N_real − mean)/(N_real + mean + 4);
  - concentration;
  - significance profile SP = Z/‖Z‖;
  - SE(Z) ≈ √((1 + Z²/2)/R).
- **Run checks** (D55). N_real must equal the igraph count of the input network, identified by its SHA-256.
  Each run must also have exit status 0, a complete `run.log`, no flags, and Z matching mfinder's.
- **Comparison** (`[motif_final]`, D56–D57). With networks of 5–16 million triads, nearly every triad is
  significant, so translations are compared by SP shape:
  - the Pearson correlation of the 13-triad SP vectors, average linkage on 1 − r;
  - Spearman ρ and Euclidean distance as robustness checks;
  - the motifs with the largest SP range across texts.
  - Stability: Ru1 at R = 100 vs R = 1000.

Report: `docs/motifs/motif_report.md`. Amendment §5, §10.

## 6. Betweenness centrality (`src/huckfinn_cna/centrality.py`)

- **Inputs.** The frozen networks-v1 files, checked against the checksums (D39).
- **Variants.** Exact betweenness with igraph, normalized as networkx does (D40):
  - primary: unweighted, undirected;
  - secondary: directed;
  - sensitivity (the old method): directed, distance = 1 / bigram count.
  - igraph is cross-checked against networkx on Ru2.
- **Frequency adjustment** (Amancio 2015; `[centrality]`, D41). For each of 30 shuffles, all word tokens
  are permuted across the text and refilled into sentences of the original lengths, and the network is
  rebuilt with the same rules.
  - Adjusted score = real / mean shuffled; z = (real − mean)/SD.
  - Seeds: `default_rng([20261027, text index])`.
- **Ranking.** Adjusted ranks only among words at least as frequent as the text's 200th most frequent
  lemma (`adjusted_rank_top_frequent`, D42). Stability between variants: Spearman ρ over the union of the
  top-100 lists (D43).

Report: `docs/centrality/centrality_report.md`. Amendment §11.

## 7. Classic stylometric measures (`src/huckfinn_cna/lexical.py`)

- **Tokens.** Word tokens as Stanza lemmas, lowercased, ё → е. Two views (`[lexical]`, D31):
  - "all" (stop words kept, primary);
  - "content" (UPOS in `[tag].content_upos`).
- **Measures** (D32): MSTTR (2,000-word chunks), MATTR (windows 100/500/1,000, primary 500), MTLD
  (bidirectional, 0.72), HD-D (42 draws), Yule's K, Honoré's R (the old "W") and unigram entropy (log₂).
  MTLD, HD-D and MATTR are cross-checked against `lexicalrichness`.
- **Length control** (D33). Equal-length samples of N tokens, where N is the shortest text's count
  rounded down to 1,000 (all 48,000; content 25,000). Samples start at a paragraph start.
  - Each text contributes its non-overlapping samples.
  - Each text except Ru2 also contributes 200 random windows.
  - **Half-length windows** (D62): 200 random windows of N // 2 tokens for every text, Ru2 included, so
    that Ru2 also gets a spread. Ru2's windows come from a text only twice their length, so its spread is
    understated.
- **Sentence length** (D34). Word tokens per Stanza sentence (punctuation excluded): mean, median and IQR,
  plus punctuation marks per 1,000 words.
- **Uncertainty** (D35). Paragraph bootstrap (1,000 resamples) for sentence length, Yule's K and HD-D. For
  MSTTR, MATTR, MTLD, Honoré's R and entropy the bootstrap is biased, so their uncertainty is the spread of
  the random windows.
  - Seeds: `default_rng([20261026, stream, text index])`, streams `bootstrap_stream`, `windows_stream`,
    `half_windows_stream`.
- **Old values.** The exact old values are in `[lexical.old]` (D46).

Report: `docs/classic/classic_report.md`. Amendment §7.

## 8. Delta, clustering and embedding (`src/huckfinn_cna/delta.py`)

- **Units** (`[delta]`, D47).
  - Blocks: paragraph-aligned 5,000-word blocks (`block_words`; 79 blocks in all).
  - Whole texts.
  - Relative frequencies of the most frequent lemmas, ranked by mean block relative frequency.
  - z-scores use means and SDs over all blocks.
- **Distances and clustering** (D48).
  - Cosine Delta (primary) and Classic Delta.
  - Grid: 100–1,000 MFW × culling 0/100% × two distances, giving 40 trees.
  - Average linkage (primary); complete linkage and Ward (on L2-normalized vectors) as checks.
  - Summaries: a majority-rule consensus tree and an Eder-style consensus network.
  - Primary setting: all words, 300 MFW, culling 0%, Cosine Delta.
- **Block diagnostics** (D49). Same-translation nearest neighbours, silhouette, story-position gap, and
  shared word n-grams between texts (n = 5, 8).
- **Embedding** (D53–D54). The main-text Figure 8 is a spectral embedding of the Cosine Delta distances,
  at text and at block level:
  - Gaussian kernel W = exp(−d²/(2σ²)), σ = median off-diagonal distance;
  - symmetric normalized Laplacian;
  - stability checked across σ × 0.5/1/2 and across random states by Procrustes disparity.
  - Appendix robustness checks: classical MDS of the texts and PCA of the blocks (`pca_mfw`, D50).
  - The old Figure 8 used a separate TF-IDF matrix, not the Figure 7 matrix; it is recomputed exactly
    for comparison (D51).

Report: `docs/delta/delta_report.md`. Amendment §8.

## Randomness and seeds

Every random step is seeded from config, and its results do not depend on the number of workers.

| Step | Seed | Config |
|---|---|---|
| Cleaning review samples | 20261019, 20261020, 20261021 | `[clean.split_words]`, `[clean.attested_joins]` |
| POS bootstrap | 20261022 | `[pos_check].bootstrap_seed` |
| mfinder final runs and length-matched runs | master 20261023 | `[mfinder.final]`, `[mfinder.lengthmatched]` |
| Classic bootstrap and windows | master 20261026 | `[lexical].master_seed` |
| Betweenness shuffles | master 20261027 | `[centrality].master_seed` |
| Spectral embedding | 20261027 | `[delta].spectral_random_state` |

With these seeds, every report except the motif report should be reproducible byte for byte from the raw
texts with the locked environment; `scripts/reproduce.sh --quick` checks this. On another platform a tagger
or BLAS difference could change a last digit, and the check would show it. The motif report depends on the R = 1000 mfinder runs, which
are reproducible from their recorded seeds but take about 1.5 days.

## Old-versus-new comparisons

The old values come from three places:
- the old repository at `[paths].old_repo` (tag `report-v3` plus the old local working folder), read only;
- `docs/audit/report3_fig9_zscores.csv` (Figure 9);
- `[lexical.old]`.

The centrality and Delta stages read the old repository to recompute the old values exactly (D44, D51), so
those two stages need it to be present.
