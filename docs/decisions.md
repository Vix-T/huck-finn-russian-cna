# Decision log

Methods decisions made by the project owner, in the order they were made. Each entry says what was
decided and where it is implemented. Parameters live in `config/pipeline.toml`; METHODS.md (when it
exists) is the source of truth and should absorb these entries.

## Input handling (2026-10-19, from the data audit)

- **I1. Encoding.** Raw files are read with encoding `utf-8-sig`, which strips the BOM.
- **I2. Line endings.** CRLF (and any lone CR) are normalized to `\n` in memory. Raw files are never rewritten.
- **I3. Paragraphs.** A paragraph is a non-empty line, not a blank-line-separated block. The audit confirmed
  one paragraph per line in all five texts.

Implemented in `scripts/audit_data.py` and cleaning rule a (`src/huckfinn_cna/clean.py`).

## Text cleaning (2026-10-20)

Rules run in the order a–k given in `src/huckfinn_cna/clean.py`; every change is logged per text in
`data/interim/clean/Ru#_changes.tsv`. Text is never joined across lines.

1. **ё → е.** All ё/Ё are replaced by е/Е (rule j). ё is used too inconsistently across the five texts
   (0.02–0.35 per 1,000 words) to carry information, and it makes «все»/«всё» unresolvable anyway.
2. **Dictionary-based split-word repair, logged and hand-sampled** (rule i). Adjacent Cyrillic tokens A B
   are joined into AB when pymorphy3 `word_is_known(AB)` is true (dictionary, not prediction) and at least
   one of A, B is not known; repeated per line until nothing changes, so three-way splits join stepwise.
   Pairs where A, B and AB are all known (e.g. «по том»/«потом») are not joined; they are listed as
   candidates for review. A seeded random sample of joins per text is reviewed by hand.
3. **Hyphen rejoin only for a particle list and по-** (rule h). «word- particle» is rejoined only for the
   particles то, либо, нибудь, ка, таки; «по- X» only when X ends in -ому, -ему, -ски, -цки or -ьи.
   Other broken hyphenations (e.g. «кое- что», «из- под») are left and counted in the cleaning report.
4. **Latin look-alikes → Cyrillic; standalone «II» → «И»** (rules e, f). Inside tokens that contain at
   least one Cyrillic letter, Latin a e o p c x y k A B E K M H O P C T X become their Cyrillic twins.
   Tokens that still mix scripts are listed for manual correction.
5. **Canonical dashes, quotes, ellipsis, whitespace** (rules d, g). Tabs → space, runs of spaces
   collapsed, whitespace stripped at both ends of each line (including paragraph indentation); «--» and spaced dashes/hyphens between words → « — »; all quote styles →
   « »; three or more full stops → «…»; doubled commas collapsed. Hyphens inside words are untouched.
6. **Remove editorial notes containing «Прим.»** (rule c). Bracketed segments with the marker are
   removed; all other bracketed segments are kept and listed.
7. **Trim Twain's Notice and Explanatory note wherever they appear** (rule b), matched on text, never on
   line numbers. Ru1 has the Notice only (raw line 1); Ru5 has the Notice (raw lines 1–3) and the
   Explanatory note (raw lines 4–7). Ru2–Ru4 contain neither. Other title, author or «Конец»-type lines
   are not trimmed; they are listed in the cleaning report as proposals.
8. **For later stages: edge weights.** Edge weights are set to 1 for motif analysis; bigram counts are
   kept as weights for weighted betweenness.

Manual corrections go in `data/corrections/manual.tsv` (text, raw line, before, after, reason) and are
applied last (rule k); each entry must match exactly once or the run fails.
