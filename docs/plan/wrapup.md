# Plan: loose ends and reproducibility wrap-up (agreed 2026-10-29)

Don't touch `data/interim/mfinder/` or the networks (the length-matched run launches at 20:00 on
29 Oct); finish all CPU-heavy steps before 19:30.

1. Loose ends. Confirm each, and fix if missing:
   (a) the classic stage includes the half-length (≈24,000-word) window comparison, so Ru2 gets a spread;
   (b) the spectral embedding placement in Section 8 matches the last instruction (report which placement
       is currently written);
   (c) the Ru-1933 decision is recorded (state the 30% shared wording with Ru-1926 in the corpus
       description and wherever the abridged texts are compared; keep both texts);
   (d) the Amancio (2015) citation is confirmed (PLoS ONE 10(8): e0136076,
       doi:10.1371/journal.pone.0136076) with no "from memory" flag;
   (e) the amendment report notes that the spectral embedding originally used a separate TF-IDF matrix,
       not the Figure 7 matrix the article describes.
2. `METHODS.md` (repo root): the methods as actually implemented, stage by stage (input handling,
   cleaning rules, tagging, networks, motif statistics, betweenness, classic measures,
   Delta/clustering/embeddings), every parameter pointing to `config/pipeline.toml` and every decision
   to `docs/decisions.md`. Written for a reviewer, consistent with the amendment report.
3. `README.md`: what the project is; requirements (macOS/Linux, Python 3.12 via uv, Xcode CLT or a C
   compiler); setup (`uv sync --frozen`, building mfinder from `tools/mfinder` with the seed patch,
   downloading the Stanza and spaCy models); data (raw texts not redistributed: editions, expected
   filenames in `data/raw/`, SHA-256 checksums from `data/PROVENANCE.md`); how to reproduce; where the
   outputs and the amendment report live; versions and tags (report-v3 in the old repo, networks-v1,
   networks-lm-v1).
4. `scripts/reproduce.sh`: one command running the whole pipeline in order (verify raw checksums →
   clean → tag → networks (verify checksums) → classic → betweenness → delta → motifs). A `--quick` mode
   using r = 2 for mfinder, plus a check that every number not depending on the randomization matches the
   committed reports. Full mode says clearly that the r = 1000 motif runs take ~1.5 days in parallel and
   points to `run_mfinder_all.sh`.
5. Fresh-copy test: clone the repo from the local path into a temporary folder, copy `data/raw/` in,
   `uv sync --frozen`, build mfinder, download the models (one-time network approval for Hugging Face
   and GitHub), run `scripts/reproduce.sh --quick` from scratch. Report what broke and what was fixed,
   until it runs cleanly. Then delete the temporary folder.
6. Run the tests, commit (no push), summarize: loose-end status, and whether the fresh-copy quick
   reproduction passed.

## Status (2026-10-29, 19:30)

Items 1–4 and 6 done. Item 1: (a) was missing and is added (decision 62; classic stage re-run, single
process, 2 min, finished 19:26; only the new tables changed); (b) already as instructed (RBF spectral
embedding = main-text Figure 8, MDS and PCA in the appendix); (c) recorded (decision 61) and stated in
amendment §4 and at each abridged comparison; (d) already confirmed, no flag; (e) added to §8.1.
Item 5 (fresh-copy test) is CPU-heavy (tagging, betweenness shuffles, mfinder) and could not finish before
19:30, so it waits until the length-matched run has finished.

Fresh copy (19:31–19:33, in the session scratchpad): clone from the local path, raw texts copied, `uv sync --frozen`
OK, `tools/mfinder/build.sh` OK (binary SHA-256 `3241534e…` differs from the recorded `c0734777…`, newer OS and
compiler; output identity to be checked against the R = 2 rehearsal runs), Stanza models downloaded. Broke:
`tag --download` downloads and then tags, which fails before cleaning; fixed with `tag --download-only` for setup.
The `--quick` run waits until the length-matched run has finished (project owner's choice).
