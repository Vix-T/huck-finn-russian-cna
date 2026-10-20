# Amendment Report: Rebuilt Pipeline for *Comparative Stylometric Analysis of Mark Twain's "The Adventures of Huckleberry Finn" Russian Translations*

**Vix Talbot**
10-2026 (last updated 2026-10-20)

Companion to *Final Report 3*¹ (11-2025). This is a living report: a section is added at the end of
each pipeline stage, and the Executive Summary is updated with it.

---

## 1. Executive Summary

We rebuilt the analysis behind *Final Report 3* as a reproducible pipeline. Two stages are complete:
the data audit with text preparation, and an audit of the previous motif results.

**Text preparation** changes how the article should describe its data, but no reported number has
changed yet. The 1911 Engel'gardt text (Ru-1911) contains OCR damage, mainly words split by stray
spaces. We repaired it, which reduces its vocabulary by 382 word types (1.8%). In the other four
texts the change is at most 42 types (0.3%). We also found that the old cleaning step deleted the
letter ё and fused hyphenated words, and we have replaced it. All article numbers are recomputed in
the stages that follow, and each stage will report its old and new values.

**The motif audit** confirms that Figure 9 comes from one specific mfinder run: all 65 values match.
It also corrects four statements in the article:

- The archived outputs contain two mfinder runs from 2025 on identical input networks and settings.
  Their Z-scores differ by up to 1.52 for the same motif and text, which reflects run-to-run
  randomization noise.
- Braude's network is not the largest, so network size does not explain its large Z-scores.
- mfinder does not use edge weights.
- mfinder's motif member lists are partial samples. It does not erase them.

These need wording changes in the Data Pipeline section, §3.4.1 and §3.5. The motif figures
(Figures 9–11) will be regenerated in the Motif Analysis stage.

### Changes at a glance

| Stage | Change | Impact level | Article section affected |
|---|---|---|---|
| Data Audit and Text Preparation | Input handling: byte-order mark removed, line endings normalized | None | — |
| Data Audit and Text Preparation | Orthography checked: all five texts use modern spelling, no conversion needed | None | — |
| Data Audit and Text Preparation | OCR repair: split words rejoined (mainly Ru-1911), Latin look-alike letters replaced, broken hyphenation rejoined | Wording | Data Pipeline |
| Data Audit and Text Preparation | Nine inline editorial notes removed from Ru-1911 | Wording | Data Pipeline |
| Data Audit and Text Preparation | Twain's Notice (Ru-1911, Ru-1960) and Explanatory note (Ru-1960) removed | Wording | Data Pipeline |
| Data Audit and Text Preparation | Dashes, quotation marks and ё normalized | Wording | Data Pipeline |
| Data Audit and Text Preparation | Old cleaning step deleted ё and fused hyphenated words; replaced | Wording | Data Pipeline ("Distinct Full Datasets") |
| Audit of Previous Motif Results | Figure 9 traced to one mfinder run (65 of 65 values match) | None | §3.4.1, Figure 9 |
| Audit of Previous Motif Results | Two archived 2025 mfinder runs with identical inputs and settings differ by up to 1.52 in Z | Wording | §3.4.1 |
| Audit of Previous Motif Results | Braude's network ranks 3rd of 5 by edges; the "more connections" explanation is withdrawn | Wording | §3.4.1 (Figure 9 discussion) |
| Audit of Previous Motif Results | mfinder ignores edge weights | Wording | Data Pipeline; §3.4.1 |
| Audit of Previous Motif Results | Motif member lists are partial samples from a different run | Wording | §3.5 |

---

## 2. Purpose of the Amendment

Before submission, we rebuilt the analysis as a reproducible pipeline. It has a pinned software
environment, a single configuration file for every parameter, seeded randomness, automated tests and
one command to run each stage. With these, peer reviewers can clone the repository and reproduce
every result in the article.

Reviewing each step during the rebuild turned up a number of corrections. This report documents
them stage by stage. For each correction it gives the old value or practice, the new one and the
reason, so that the article can be brought in line with the rebuilt pipeline before submission.

---

## 3. How to Read This Report

Every change is assigned one impact level:

| Impact level | Meaning |
|---|---|
| **None** | Internal change, no effect on the article. |
| **Wording** | A sentence or description in the article should be adjusted. Suggested replacement text is provided. |
| **Numbers** | Reported values change, but the interpretation does not. |
| **Figure** | A figure needs replacing. |
| **Finding** | An interpretation or conclusion changes. |

Each stage section lists what changed (old → new), why, the effect (with an old-versus-new table
where there are numbers), the impact level and, for Wording or higher, suggested article text.
Translations are referred to by the article's labels (Ru-1911-Engelgardt, etc.). Section and figure
numbers refer to *Final Report 3*.

---

## 4. Data Audit and Text Preparation

### 4.1 Input handling

**What changed.** We now read every raw file as UTF-8 with its byte-order mark removed. All five
files have one. Windows line endings (CRLF) are normalized to `\n` in memory, and the raw files are
never modified. A paragraph is one non-empty line, which the audit confirmed for all five texts.

**Why.** An unremoved byte-order mark becomes an invisible character attached to the first word of
the text. Fixing these rules makes every later count well defined.

**Effect.** No measurable effect on word counts.

**Impact level: None.**

### 4.2 Orthography

**What changed.** Nothing. We checked whether any text, in particular the 1911 translation, uses
pre-1918 spelling.

**Why.** Old spelling (ѣ, і, ѳ, ѵ, final ъ, endings such as -аго) would split one word into two
spellings and inflate the vocabulary of the older texts.

**Effect.** None of the five texts contains ѣ, і, ѳ or ѵ. Final ъ occurs once in Ru-1926 and once in
Ru-1933, both OCR errors. All three -аго matches in Ru-1911 are OCR splits of modern words (e.g.
«благо душествовали»), and Ru-1926 has two isolated old-style forms («животныя»). Ru-1911 uses
modern orthography, so no spelling conversion was needed.

**Impact level: None.**

### 4.3 OCR repair

**What changed.** Old: OCR errors were not repaired. New: three kinds of OCR damage are
repaired, each by a fixed rule, and every change is logged.

- *Split words.* A word broken by a stray space (e.g. «толь ко» for «только») is rejoined when the
  joined form is a word in the pymorphy3 Russian dictionary⁴ and at least one half is not. If both
  halves are dictionary words (e.g. «по том»), they are joined only when the joined form occurs as
  one word at least twice in the same translation, the split pair occurs in none of the other four,
  and neither half is a single letter. Neither rule joins after «не» or «ни», or before a capital
  letter.
- *Latin look-alikes.* Latin letters that look like Cyrillic ones (a, c, o, p, …) inside Cyrillic
  words are replaced by their Cyrillic counterparts. In Russian text, `c` is the Latin letter, not
  the Cyrillic «с».
- *Broken hyphenation.* Hyphenated words broken by a space (e.g. «кое- что», «из- под»,
  «какая- нибудь») are rejoined.

**Why.** Each split word counts as two spurious words, which inflates vocabulary-based measures such
as type counts and the type–token ratio. It also adds false edges to the word-adjacency network.
Ru-1911, an OCR scan, is the most affected.

**Effect.** Changes per text:

| Repair | Ru-1911 | Ru-1926 | Ru-1933 | Ru-1949 | Ru-1960 |
|---|---|---|---|---|---|
| Split words rejoined (dictionary rule) | 92 | 0 | 1 | 0 | 0 |
| Split words rejoined (within-text attestation rule) | 434 | 2 | 1 | 4 | 4 |
| Latin look-alike letters replaced | 2 | 1 | 1 | 0 | 1 |
| Broken hyphenation rejoined | 0 | 2 | 22 | 3 | 40 |

Word tokens and word types (lowercased) in the raw and fully prepared texts, after all steps in
Section 4:

| Translation | Tokens, raw | Tokens, prepared | Types, raw | Types, prepared | Δ types | Δ types % |
|---|---|---|---|---|---|---|
| Ru-1911-Engelgardt | 115,056 | 114,427 | 20,876 | 20,494 | −382 | −1.8% |
| Ru-1926-Anon | 48,208 | 48,208 | 10,291 | 10,289 | −2 | −0.0% |
| Ru-1933-Chukovsky-(Ed.) | 63,217 | 63,194 | 12,689 | 12,683 | −6 | −0.0% |
| Ru-1949-Braude | 91,768 | 91,761 | 14,939 | 14,936 | −3 | −0.0% |
| Ru-1960-Daruzes | 92,298 | 92,136 | 14,399 | 14,357 | −42 | −0.3% |

Here a word token is a run of Cyrillic letters, with internal hyphens joining the parts. This is the
same definition for raw and prepared text, so the table isolates the effect of preparation. Of the
382 types removed in Ru-1911, 220 come from the attestation rule. We checked a seeded random sample
of 40 of those joins by hand, and all 40 were genuine OCR splits.

**Impact level: Wording.** See the suggested paragraph in 4.8.

### 4.4 Editorial notes

**What changed.** Old: Ru-1911 contained nine bracketed editorial notes inside the narrative, marked
«Прим.», and they were analysed as Twain's text. New: they are removed. All other bracketed passages
are kept and listed for review. None remain in any text.

**Why.** The notes are commentary added by the editor or translator, not part of the translation.

**Effect.** 9 notes removed from Ru-1911. None in the other texts.

**Impact level: Wording.** See 4.8.

### 4.5 Twain's Notice and Explanatory note

**What changed.** Old: Twain's prefatory Notice was included in Ru-1911 and Ru-1960, and his
Explanatory note in Ru-1960. New: both are removed wherever they appear. They are matched on their
text, never on line numbers.

**Why.** Comparability. Ru-1926, Ru-1933 and Ru-1949 contain neither passage, so keeping them would
give two translations text the others lack.

**Effect.**

| Translation | Removed | Raw lines |
|---|---|---|
| Ru-1911-Engelgardt | Notice | 1 |
| Ru-1960-Daruzes | Notice; Explanatory note | 1–3; 4–7 |

Closing lines such as «Конец» or the signature «Гек Финн» are left in place.

**Impact level: Wording.** See 4.8.

### 4.6 Dashes, quotation marks and ё

**What changed.** New: punctuation is normalized to one form per mark. Double hyphens and spaced
dashes become an em dash, all quotation styles become « », and runs of three or more full stops
become an ellipsis. Whitespace is normalized. ё is written as е throughout.

**Why.** The five editions use different typographic conventions. Ru-1911, for example, uses `--`
for dashes and straight `"` quotes, the others em dashes and « ». Sentence splitting and
average sentence length depend on these marks. ё is used inconsistently (0.02 to 0.35 per 1,000
words), so «все» and «всё» would otherwise count as different words in some editions and not in
others.

**Effect.**

| Normalization | Ru-1911 | Ru-1926 | Ru-1933 | Ru-1949 | Ru-1960 |
|---|---|---|---|---|---|
| Punctuation changes | 2,352 | 155 | 520 | 121 | 204 |
| ё → е | 4 | 1 | 22 | 3 | 23 |

**Impact level: Wording.** See 4.8.

### 4.7 The old cleaning step

**What changed.** Old: the text was lowercased and then cleaned with the regular expression
`[^а-я\s]` (`01dataprep_clean_lemmatize.py`), which deletes every character outside а–я and
whitespace. New: the rules in 4.1–4.6, with ё kept until it is mapped to е and hyphenated words kept
as single words.

**Why.** ё lies outside the range а–я, so the old expression deleted it rather than converting it.
«её» became «е» and «ещё» became «ещ». The hyphen was deleted too, so hyphenated words were fused:
«кое-что» became «коечто» and «по-моему» became «помоему». These fused forms are not dictionary
words, so lemmatization could not recover them.

**Effect.** Raw-text occurrences affected by the old expression:

| | Ru-1911 | Ru-1926 | Ru-1933 | Ru-1949 | Ru-1960 |
|---|---|---|---|---|---|
| Words containing ё (letter deleted) | 4 | 1 | 22 | 3 | 23 |
| Hyphenated words (fused) | 1,078 | 397 | 538 | 855 | 1,143 |

Each fused form (e.g. «коечто», «когото») is a word that does not exist in Russian. The old
lemmatizer (pymorphy2) cannot find it in its dictionary, so inflected forms of the same hyphenated
word are not reliably grouped under one lemma. The fusion therefore changes the old type counts of
all five texts: 1,078 fused hyphenated words in Ru-1911, 397 in Ru-1926, 538 in Ru-1933, 855 in
Ru-1949 and 1,143 in Ru-1960. Their effect on the lexical-richness numbers (Yule's K, Honoré's W, type–token ratio,
unigram entropy) will be quantified in the Classic Stylometric Measures stage (Section 7), where the
impact level will be confirmed.

**Impact level: Wording.** The article's description of "clean" data in "Distinct Full Datasets"
should describe the new procedure (see 4.8).

### 4.8 Suggested article text

Add the following paragraph to the **Data Pipeline** section, under "Distinct Full Datasets", in
place of the sentence beginning "For the purposes of the report 'clean' data …":

> **Text preparation.** Before any measurement, the five raw texts were prepared by a fixed sequence
> of rules, each change logged and reproducible from the project repository. Files were read as
> UTF-8, with the byte-order mark removed and line endings normalized; each line is one paragraph.
> All five texts, including the 1911 translation, use modern Russian orthography, so no spelling
> conversion was needed. The Engel'gardt text (Ru-1911) was digitized by optical character
> recognition and contains words split by stray spaces (e.g. «толь ко» for «только»). We rejoined
> these using the pymorphy3 Russian morphological dictionary (Korobov 2015) and, where both halves of a split are
> themselves words, only when the joined form occurs elsewhere in the same translation and the split
> form occurs in none of the others. We also replaced Latin look-alike letters inside Cyrillic words
> and rejoined hyphenated words broken by a space. Nine editorial notes inside the Ru-1911 narrative
> were removed, as were Twain's Notice (Ru-1911, Ru-1960) and Explanatory note (Ru-1960), which the
> other translations do not include. Dashes, quotation marks and ellipses were normalized, ё was
> written as е throughout, and hyphenated words (e.g. «кое-что») were kept as single words. These
> steps reduced the vocabulary of Ru-1911 by 382 word types (1.8%) and of the other translations by
> at most 42 types (0.3%). Lowercasing, lemmatization and stop-word removal follow as described
> below.

Add Korobov (2015)⁴ to the article's bibliography.

---

## 5. Audit of Previous Motif Results

This section audits the mfinder outputs behind §3.4.1 and §3.5 as they stand in the archived
repository (tag `report-v3`). It does not recompute any motif statistics; the rebuilt motif analysis
is reported in Section 10.

### 5.1 Source of Figure 9

**What changed.** Nothing in the values. We identified which mfinder output produced Figure 9.

**Why.** The archived outputs contain two mfinder runs from 2025 on identical input networks and
settings (100 randomizations): the run behind Figure 9 (`*_input_OUT.txt`, Aug–Oct 2025) and a second
run (`*_stats_OUT.txt`, Oct 23–25, 2025). Each run drew its own random networks.

**Effect.** We compared all 65 values in Figure 9 (13 motifs × 5 translations) with both runs, after
rounding to two decimals:

| mfinder run | Values matching Figure 9 |
|---|---|
| `input_OUT` | 65 / 65 |
| `stats_OUT` | 2 / 65 |

Figure 9 is the `input_OUT` run.

**Impact level: None.**

### 5.2 Run-to-run variation of Z-scores

**What changed.** Old: the article presents the Z-scores as fixed values. New: we report that they
come from a single run of mfinder with 100 random networks and that the archived outputs show how
much they vary between runs. No new mfinder run was made for this section.

**Why.** mfinder estimates each Z-score from randomized networks³, so runs with the same input and
settings give different values. The archived outputs contain two mfinder runs from 2025 on identical
input networks and settings (3-node motifs, 100 randomizations): the run behind Figure 9
(`_input_OUT`, Aug–Oct 2025) and a second run (`_stats_OUT`, Oct 23–25, 2025). Their Z-scores differ
by up to 1.52 for the same motif and text, which reflects run-to-run randomization noise.

**Effect.** Largest absolute difference between the two archived 2025 runs, over the 13 motifs:

| | Ru-1911 | Ru-1926 | Ru-1933 | Ru-1949 | Ru-1960 |
|---|---|---|---|---|---|
| max \|ΔZ\| | 1.46 | 0.43 | 1.41 | 1.27 | 1.52 |

The figure stars a value when |Z| > 2.0. Exactly one cell changes status between the two archived
runs: Ru-1949, motif 110 (Z = 1.82 in Figure 9, 2.07 in the `_stats_OUT` run).

**Impact level: Wording.**

**Suggested article text.** §3.4.1 now says: "Then it creates 100 simulations while preserving the
exact node, edge, and degree counts of the original text but randomly shuffles the specific
connections between words." Add after it:

> The Z-scores in Figures 9–11 come from a single mfinder run with 100 random networks. Because the
> random networks differ between runs, so do the Z-scores: a second run made in 2025 on the same
> networks with identical settings gave values that differ by up to 1.5 for the same motif and
> translation.

The Figure 9 discussion now says: "This step remains necessary however, as it's part of calculating
the raw z-scores with a 95% confidence interval which tells us which outcomes are statistically
significant. The star following a z-score tells you if it is one of the significant scores." Replace
with:

> A star marks a Z-score with |Z| > 2.0, roughly the two-sided 5% level under a normal approximation.

### 5.3 Network size and Braude's Z-scores

**What changed.** Old: the article attributes the size of Braude's Z-scores to network size. New:
that explanation is withdrawn.

**Why.** Braude's network is not the largest. Network sizes from the archived mfinder input files:

| Rank by edges | Translation | Nodes | Edges | Edges per node | Total edge weight |
|---|---|---|---|---|---|
| 1 | Ru-1911-Engelgardt | 9,822 | 67,577 | 6.88 | 114,962 |
| 2 | Ru-1960-Daruzes | 7,335 | 51,216 | 6.98 | 92,108 |
| 3 | Ru-1949-Braude | 7,634 | 49,719 | 6.51 | 91,533 |
| 4 | Ru-1933-Chukovsky-(Ed.) | 6,665 | 40,160 | 6.03 | 62,997 |
| 5 | Ru-1926-Anon | 5,836 | 31,254 | 5.36 | 47,758 |

Braude ranks third by edges, by edges per node and by total edge weight, and second by nodes.

**Effect.** The values in Figure 9 are unchanged. Only the explanation offered for them is affected.

**Impact level: Wording.**

**Suggested article text.** In the Figure 9 discussion, replace "This is because that network
contains more connections overall." with:

> This is not a matter of network size: by number of connections, Braude's network ranks third of
> the five, behind Engel'gardt and Daruzes.

In the Discussion table, the description of the Raw Motif Z-Score Heatmap ("while being influenced
by network size") can stay. Raw Z-scores do depend on network size in general, which is why the
article normalizes them in Figure 10 using the significance profile of Milo et al.⁵

### 5.4 Edge weights

**What changed.** Old: the article describes edge weights as part of the network mfinder analyses. New:
we state that mfinder counts motifs on the unweighted network.

**Why.** The mfinder input files carry bigram counts as a third column (from 1 to 298). The mfinder
1.2 manual, §2.4, states: "In the current version <edge weight> is ignored and should be 1 for all
edges."² The manual documents no option for using weights, and the old command
(`mfinder <input> -s 3 -r 100 -omem -f <output>`, in `18run_mfinder.sh`) passed none. Motif counts and
Z-scores therefore depend only on which directed edges exist, not on how often each was traversed. The rebuilt
pipeline sets motif edge weights to 1 and keeps bigram counts only where a weighted measure uses them.

**Effect.** None on the archived values, which were computed as mfinder computes them. Only the
description is affected.

**Impact level: Wording.**

**Suggested article text.** In the **Data Pipeline** section, replace "An edge that's traversed often,
like "Huck" following "and" becomes a very thick, strong line." with:

> We record how often each edge is traversed, so that "Huck" following "and" 500 times gives an edge
> of weight 500; the motif analysis uses only whether an edge exists, not its weight.

In §3.4.1, replace "Mfinder … takes our raw text line of the translation's graphs that we have
compiled into a dataset, where a connection like "Huck" to "and" that appears say 500 times, becomes
a simple line of code, 1 2 500." with:

> mfinder takes each translation's network as a list of directed edges between numbered words. It
> counts motifs on the unweighted network: an edge is either present or absent, however often the
> word pair occurs.

### 5.5 Motif member lists

**What changed.** Old: §3.5 states that mfinder erases which words make up each motif occurrence.
New: mfinder can write member lists (option `-omem`), and the archived repository contains them, but
they are partial.

**Why.** Each member file is headed "Partial list of N members". N is identical in all five
translations for every motif (e.g. 1,001 for motif 238, 6,006 for motif 38), whereas the real motif
counts run into the thousands to millions. So the lists are fixed-size samples, not complete
inventories. They also come from the `stats_OUT` run, not the `input_OUT` run behind Figure 9.

**Effect.** None on any reported value. The article does not use the member lists.

**Impact level: Wording.**

**Suggested article text.** In §3.5, replace "Unfortunately, mfinder explicitly erases this
information instead of caching it somewhere that I could pull out." with:

> mfinder can list the members of each motif, but only as a partial sample of fixed size, so it
> cannot give a complete count of which word triples make up each motif.

### 5.6 Motif figures

Figures 9, 10 and 11 will be regenerated from the rebuilt networks in the Motif Analysis stage
(Section 10). The changes in this section concern the article's wording only.

---

## 6. Tokenization and Tagging

To be completed.

## 7. Classic Stylometric Measures

To be completed. Carried over from 4.7: quantify the effect of the old fusion of hyphenated words on
the lexical-richness numbers, and confirm the impact level.

## 8. Delta, Clustering and Embedding

To be completed.

## 9. Network Construction

To be completed.

## 10. Motif Analysis

To be completed.

## 11. Betweenness Centrality

To be completed.

---

## 12. Bibliography

1. Talbot, V. *Comparative Stylometric Analysis of Mark Twain's "The Adventures of Huckleberry Finn"
   Russian Translations: Classic & Novel Approaches* (Final Report 3). (2025).
2. Kashtan, N., Itzkovitz, S., Milo, R. & Alon, U. *Network Motif Detection Tool: mfinder Tool
   Guide*, version 1.2. Weizmann Institute of Science (2005). Bundled with pymfinder:
   https://github.com/stoufferlab/pymfinder (`pymfinder/mfinder/mfinder-manual.pdf`).
3. Kashtan, N., Itzkovitz, S., Milo, R. & Alon, U. Efficient sampling algorithm for estimating
   subgraph concentrations and detecting network motifs. *Bioinformatics* 20, 1746–1758 (2004).
4. Korobov, M. Morphological Analyzer and Generator for Russian and Ukrainian Languages. In
   *Analysis of Images, Social Networks and Texts (AIST 2015)*, Communications in Computer and
   Information Science 542, 320–332 (2015). Used via pymorphy3 2.0.6.
5. Milo, R. et al. Superfamilies of evolved and designed networks. *Science* 303, 1538–1542 (2004).
   Source of the significance-profile method.
