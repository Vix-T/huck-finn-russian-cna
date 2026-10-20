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

## Cleaning follow-up (2026-10-20)

9. **Split-word guards** (rule i; config `clean.split_words`). Never join when B starts with a capital
   letter (A may be capitalized, e.g. at sentence start), and never join when A is «не» or «ни»: those are
   spelling choices, not OCR damage. Blocked pairs are listed with the guard as the reason. Known cost:
   the guard on «не» also blocks the genuine OCR split «не годяю» → «негодяю» (Ru1).
10. **More hyphen rejoins** (rule h; config `clean.hyphenation`). «кое- X» → «кое-X» for any word X, and
    «из- под», «из- за» → «из-под», «из-за», in any spacing variant. Rule g leaves a fully spaced hyphen
    alone when a rule h pattern would rejoin it, so rule h sees every variant.
11. **Manual corrections** (rule k): undo the wrong join «по сту» → «посту» (Ru3), fix «nо-овечьи» →
    «по-овечьи» (Ru1) and Latin «c» → «с» in «c герцогом» (Ru2). The guards in 9 already prevent the wrong
    joins «ДонКи» and «аБуд». Re-cleaning a cleaned text re-does a join that a manual entry undid; the
    idempotence check allows exactly those changes.
12. **Rule i2 exists but is OFF pending review** (config `clean.attested_joins.apply = false`). It would
    join A B → AB when AB occurs as one token at least `min_attested` times in the same text, the pair
    «A B» occurs in none of the other four texts, and the rule i guards allow it. Each run writes a preview
    (`data/interim/clean/review/i2_*.tsv`) and its projected effect in the cleaning report; the cleaned
    texts are not changed. *Superseded by 13: i2 is now ON.*
13. **Rule i2 ON, with a single-letter guard** (2026-10-20; config `clean.attested_joins`). i2 never joins
    when A or B is a single letter (`min_part_letters = 2`): pairs such as «в место», «убил и», «с на» are
    normal Russian as two words. Ordinary rule i guards still apply. With the guard, `apply = true`: the
    cleaned texts include i2 joins (Ru1 434, Ru2 2, Ru3 1, Ru4 4, Ru5 4). Each run writes a seeded sample
    of applied Ru1 joins with 40 characters of context (`i2_applied_sample_Ru1.tsv`, 40 joins,
    seed 20261021) for hand-checking.

Manual corrections go in `data/corrections/manual.tsv` (text, raw line, before, after, reason) and are
applied last (rule k); each entry must match exactly once or the run fails.
