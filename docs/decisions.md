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

## Tokenization and tagging (2026-10-20)

Implemented in `src/huckfinn_cna/tag.py` (tagging) and `src/huckfinn_cna/pos_check.py` (POS re-check);
parameters in config `[tag]` and `[pos_check]`.

14. **Taggers and exact versions.** Stanza **1.15.0** with Russian model resources **1.15.0**
    (packages: tokenize `syntagrus_nocharlm`, pos `syntagrus_charlm`, lemma `syntagrus_nocharlm`; the
    pos model also loads `pretrain/conll17` and the `newswiki` character LMs). spaCy **3.8.16** with
    `ru_core_news_lg` **3.8.0**, a pinned direct-URL dependency (the version listed for spaCy 3.8 in
    spaCy's compatibility table). Stanza models are stored in `models/stanza/` (config
    `paths.stanza_model_dir`, gitignored) and fetched with `uv run python -m huckfinn_cna.tag --download`.
    `tag.py` refuses to run if the installed versions differ from config. Inference has no randomness,
    so no seed applies.
15. **Tokens and sentences.** Stanza tokenizes and splits sentences per paragraph (one non-empty line of
    the cleaned text); no sentence crosses a paragraph. **Hyphenated words stay single tokens** (project
    owner's decision, 2026-10-20): Stanza's tokenizer splits 12–21% of them (mostly -то, -ка and
    compounds such as мало-помалу), so pieces lying inside one hyphenated word with no space are merged,
    and Stanza then tags and lemmatizes the merged tokens as pre-tokenized sentences. A letter run that
    touches a dangling hyphen (OCR line-break residue left by cleaning, e.g. «Алексан- дер» in Ru5) is
    not treated as a hyphenated word and is left as Stanza tokenizes it.
16. **Cross-check.** spaCy receives exactly Stanza's tokens (with the original spacing), not its own
    tokenization; components excluded: `ner`.
17. **Integrity checks** (run fails otherwise): concatenated tokens equal the paragraph without
    whitespace, each token matches the text at its offsets, and no hyphenated word is split.
18. **Columns.** `para_id`: 1-based index of the paragraph (non-empty line); `sent_id`: 1-based across
    the text; `tok_id`: 1-based within the sentence. `is_word`: letters only, any script, internal
    hyphens allowed. `is_punct`: every character in a Unicode punctuation category. `is_content`: Stanza
    UPOS in `tag.content_upos` (NOUN, VERB, ADJ, ADV, PROPN). `verbform` / `spacy_verbform`: the VerbForm
    value from each tagger's features. `spacy_feats` is kept so the VerbForm breakdown can be given for
    both taggers.
19. **Cache.** A text is re-tagged only if the SHA-256 of its cleaned file plus the `[tag]` config
    changes (`data/interim/tagged/Ru#.meta.json`). Code changes are not part of the key; delete the
    tagged files to force a re-run.
20. **POS re-check definitions.** Word tokens only (`is_word`), stop words kept. Ranks are competition
    ranks over all UPOS tags in a text. The heatmap columns are the ten tags with the highest summed
    count over all texts, as in the old Figure 6. VerbForm breakdown over VERB and AUX tokens.
    Agreement is the share of word tokens with identical UPOS from both taggers.
21. **Running uv inside the Claude Code sandbox.** Because `ru_core_news_lg` is a direct-URL dependency,
    plain `uv run` re-checks its URL on github.com; inside the sandbox use `uv run --frozen`, which
    installs from `uv.lock` without network access.
22. **Uncertainty for the POS re-check** (2026-10-20; config `[pos_check]`). Paragraph-level bootstrap:
    paragraphs resampled with replacement, 1,000 resamples, percentile 95% intervals, seed **20261022**
    (numpy `default_rng([seed, text index])`, one draw per text shared by both taggers). Intervals for
    NOUN : VERB, NOUN : (VERB + AUX), and the finite, participle and verbal-adverb (Conv) shares of
    VERB and AUX tokens. Two values are called distinguishable when their intervals do not overlap
    (conservative). Compared: Braude vs Daruzes (`compare_pair`) and Ru1 vs Ru5 (`trend_endpoints`).

## Network construction and mfinder (2026-10-21)

Implemented in `src/huckfinn_cna/networks.py`, `tools/mfinder/` and `scripts/run_mfinder.sh`;
parameters in config `[networks]` and `[mfinder]`.

23. **Lemmas as nodes.** One directed network per text. Nodes are Stanza lemmas (column `lemma`) of
    word tokens (`is_word = 1`), lowercased. Stanza writes «всё» as a lemma although the cleaned text
    has no ё, so lemmas are mapped ё → е (`networks.lemma_char_map`; project owner's decision,
    2026-10-21, in line with decision 1); this merges «всё» into «все». Every word lemma is a node,
    including the 9–13 per text that end up with no edge.
24. **Sentence-bounded edges.** Within each sentence (Stanza `sent_id`), each word links to the next
    word. Tokens that are not words (punctuation, and the 14–72 numbers, abbreviations and OCR
    fragments per text) are skipped, so words on either side of a comma are linked. No edge crosses a
    sentence boundary. Self-loops are dropped and counted. Edge counts are kept for weighted measures
    (decision 8); the motif graph is unweighted and mfinder's weight column is 1.
25. **Python is the main engine; mfinder is a cross-check.** The 13 connected 3-node triads are
    counted exactly with igraph (`motifs_randesu`, size 3) and networkx (`triadic_census`); the run
    fails unless they agree. Triad classes are labelled with mfinder subgraph IDs, decoded from
    mfinder's scheme (ID = Σ a[i][j]·2^(3i+j), smallest over vertex orderings) and stored in config
    `[[networks.triads]]` by `python -m huckfinn_cna.networks --triad-table`; a test checks the stored
    table against the decoding, and the mfinder tests check it against mfinder's own output.
26. **mfinder source and seed patch.** mfinder 1.2 is vendored in `tools/mfinder/src` from the
    original import in the pymfinder repository (commit `62acf97`), not pymfinder's later modified
    copy, which disables output files and runs 10× the edge switches per random network
    (`tools/mfinder/README.md`). The only change is `tools/mfinder/seed.patch`: the random seed is
    read from `MFINDER_SEED` (integer 1..2147483646; the run fails if it is missing or invalid)
    instead of the clock. Flags: `-s 3 -r N -f NAME -q -orall`; `-orall` keeps every random
    network's counts for empirical p-values. `-omem` and `-nu` are not used.
