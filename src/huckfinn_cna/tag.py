"""Tokenization and tagging: data/interim/clean/Ru#.txt -> data/interim/tagged/Ru#.tsv.

Stanza tokenizes and splits sentences in each paragraph (one non-empty line of the cleaned
text). Pieces of a hyphenated word that Stanza's tokenizer split (Взгляни-ка -> Взгляни / - / ка)
are merged back into one token, and Stanza then tags (UPOS, features) and lemmatizes those
tokens as pre-tokenized sentences. spaCy tags exactly the same tokens as a cross-check. Integrity checks fail the run if any character is lost or added, or if a
hyphenated word is split. A text is re-tagged only when its cleaned file or the [tag]
config changes (hash recorded in Ru#.meta.json).

Usage:
    uv run python -m huckfinn_cna.tag [--config PATH] [--download]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import unicodedata
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

from huckfinn_cna.config import DEFAULT_CONFIG, load_config, repo_path

COLUMNS = ["text", "para_id", "sent_id", "tok_id", "token", "lemma", "upos", "feats", "verbform",
           "is_word", "is_punct", "is_content", "spacy_upos", "spacy_lemma", "spacy_feats", "spacy_verbform"]

# A word: letters only, internal hyphens joining parts (что-то, кое-как). Any script.
WORD_RE = re.compile(r"[^\W\d_]+(?:-[^\W\d_]+)*")
# Hyphenated words in the text, merged into and checked to be single tokens. A run that touches a
# dangling hyphen (OCR line-break residue left by cleaning, e.g. «Алексан- дер») is not a word.
HYPHENATED_RE = re.compile(r"(?<![\w-])[^\W\d_]+(?:-[^\W\d_]+)+(?![\w-])")


class TaggingError(Exception):
    pass


@dataclass
class Token:
    text: str
    start: int          # character offsets in the paragraph
    end: int
    sent_id: int        # 1-based within the text
    tok_id: int         # 1-based within the sentence
    lemma: str
    upos: str
    feats: str
    spacy_upos: str = ""
    spacy_lemma: str = ""
    spacy_feats: str = ""


# --- Small helpers ------------------------------------------------------------


def read_paragraphs(path: Path) -> list[str]:
    """Non-empty lines of a cleaned text; each is one paragraph."""
    return [ln for ln in path.read_text(encoding="utf-8").split("\n") if ln.strip()]


def is_word(token: str) -> bool:
    return WORD_RE.fullmatch(token) is not None


def is_punct(token: str) -> bool:
    return bool(token) and all(unicodedata.category(ch).startswith("P") for ch in token)


def verbform(feats: str) -> str:
    """Value of VerbForm in a UD feature string ('' if absent)."""
    for item in feats.split("|") if feats else []:
        key, _, value = item.partition("=")
        if key == "VerbForm":
            return value
    return ""


def spaces_after(tokens: list[Token], paragraph: str) -> list[bool]:
    """Whether whitespace follows each token in the paragraph (for spaCy's Doc)."""
    return [tok.end < len(paragraph) and paragraph[tok.end].isspace() for tok in tokens]


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cache_key(clean_path: Path, tag_cfg: dict) -> str:
    """Hash of the cleaned file and the [tag] config; a change in either forces re-tagging."""
    h = hashlib.sha256(clean_path.read_bytes())
    h.update(json.dumps(tag_cfg, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    return h.hexdigest()


# --- Integrity checks ---------------------------------------------------------


def check_characters(paragraph: str, tokens: list[Token]) -> None:
    """Concatenated tokens must equal the paragraph with whitespace removed."""
    expected = "".join(paragraph.split())
    got = "".join(t.text for t in tokens)
    if got != expected:
        i = next((k for k, (a, b) in enumerate(zip(got, expected)) if a != b), min(len(got), len(expected)))
        raise TaggingError(f"characters lost or added near position {i}: "
                           f"tokens {got[max(0, i - 20):i + 20]!r} vs text {expected[max(0, i - 20):i + 20]!r}")
    for t in tokens:
        if paragraph[t.start:t.end] != t.text:
            raise TaggingError(f"token {t.text!r} does not match text at {t.start}–{t.end}")


def split_hyphenated(paragraph: str, tokens: list[Token]) -> list[str]:
    """Hyphenated words in the paragraph that are not inside a single token."""
    spans = [(t.start, t.end) for t in tokens]
    return [m.group(0) for m in HYPHENATED_RE.finditer(paragraph)
            if not any(s <= m.start() and m.end() <= e for s, e in spans)]


# --- Taggers ------------------------------------------------------------------


def check_versions(cfg: dict) -> None:
    installed = {"stanza": version("stanza"), "spacy": version("spacy"), cfg["spacy_model"]: version(cfg["spacy_model"])}
    wanted = {"stanza": cfg["stanza_version"], "spacy": cfg["spacy_version"], cfg["spacy_model"]: cfg["spacy_model_version"]}
    wrong = {k: (installed[k], wanted[k]) for k in wanted if installed[k] != wanted[k]}
    if wrong:
        raise TaggingError(f"installed versions differ from config (installed, config): {wrong}")


@dataclass
class StanzaPipelines:
    tokenizer: object   # tokenization and sentence splitting only
    tagger: object      # POS and lemma on pre-tokenized sentences


def load_stanza(cfg: dict, model_dir: Path) -> StanzaPipelines:
    import stanza
    common = dict(dir=str(model_dir), package=None, download_method=None, logging_level="WARN")
    packages = dict(cfg["stanza_packages"])
    return StanzaPipelines(
        tokenizer=stanza.Pipeline(cfg["stanza_lang"], processors={"tokenize": packages["tokenize"]}, **common),
        tagger=stanza.Pipeline(cfg["stanza_lang"], processors=packages, tokenize_pretokenized=True, **common),
    )


def load_spacy(cfg: dict):
    import spacy
    return spacy.load(cfg["spacy_model"], exclude=cfg["spacy_exclude"])


def download_models(cfg: dict, model_dir: Path) -> None:
    """Fetch the configured Stanza models into model_dir (network access needed)."""
    import stanza
    proxy = {k: os.environ[v] for k, v in (("http", "HTTP_PROXY"), ("https", "HTTPS_PROXY")) if v in os.environ}
    stanza.download(cfg["stanza_lang"], model_dir=str(model_dir), processors=dict(cfg["stanza_packages"]),
                    package=None, resources_version=cfg["stanza_resources_version"], proxies=proxy or None)


def merge_hyphenated(paragraph: str, sentences: list[list[tuple[int, int]]]) -> list[list[tuple[int, int]]]:
    """Merge token spans that lie inside one hyphenated word into a single span.

    Stanza's tokenizer splits some hyphenated words (Взгляни-ка -> Взгляни / - / ка). A merged token
    belongs to the sentence of its first piece; sentences left empty are dropped.
    """
    words = [m.span() for m in HYPHENATED_RE.finditer(paragraph)]
    flat = [(i, s, e) for i, sent in enumerate(sentences) for s, e in sent]
    out = [[] for _ in sentences]
    k = 0
    while k < len(flat):
        i, s, e = flat[k]
        word = next(((ws, we) for ws, we in words if ws <= s and e <= we), None)
        j = k
        if word is not None:
            while j + 1 < len(flat) and word[0] <= flat[j + 1][1] and flat[j + 1][2] <= word[1]:
                j += 1
        out[i].append((s, flat[j][2]))
        k = j + 1
    return [sent for sent in out if sent]


def stanza_tag(pipes: StanzaPipelines, paragraphs: list[str]) -> list[list[Token]]:
    """Stanza tokens per paragraph (hyphenated words merged); sent_id counts sentences across the text."""
    import stanza
    docs = pipes.tokenizer([stanza.Document([], text=p) for p in paragraphs])
    spans = [merge_hyphenated(p, [[(t.start_char, t.end_char) for t in s.tokens] for s in d.sentences])
             for p, d in zip(paragraphs, docs)]
    sentences = [[p[s:e] for s, e in sent] for p, para in zip(paragraphs, spans) for sent in para]
    tagged = iter(pipes.tagger(sentences).sentences)
    out, sent_id = [], 0
    for p, para in zip(paragraphs, spans):
        tokens = []
        for sent_spans in para:
            sent, sent_id = next(tagged), sent_id + 1
            if [t.text for t in sent.tokens] != [p[s:e] for s, e in sent_spans]:
                raise TaggingError(f"Stanza tagger changed the tokens of sentence {sent_id}")
            for k, ((s, e), tok) in enumerate(zip(sent_spans, sent.tokens), start=1):
                if len(tok.words) != 1:
                    raise TaggingError(f"multi-word token {tok.text!r}; not expected for Russian")
                w = tok.words[0]
                tokens.append(Token(tok.text, s, e, sent_id, k, w.lemma or "", w.upos or "", w.feats or ""))
        out.append(tokens)
    return out


def spacy_tag(nlp, paragraphs: list[str], tagged: list[list[Token]], batch_size: int) -> None:
    """Tag Stanza's tokens with spaCy (pre-tokenized input), filling the spacy_* fields."""
    from spacy.tokens import Doc
    docs = (Doc(nlp.vocab, words=[t.text for t in toks], spaces=spaces_after(toks, p))
            for p, toks in zip(paragraphs, tagged))
    for toks, doc in zip(tagged, nlp.pipe(docs, batch_size=batch_size)):
        if [t.text for t in doc] != [t.text for t in toks]:
            raise TaggingError("spaCy changed the tokenization")
        for tok, sp in zip(toks, doc):
            tok.spacy_upos, tok.spacy_lemma, tok.spacy_feats = sp.pos_, sp.lemma_, str(sp.morph)


# --- Rows and files -----------------------------------------------------------


def token_rows(text_id: str, tagged: list[list[Token]], content_upos: set[str]) -> list[list]:
    rows = []
    for para_id, toks in enumerate(tagged, start=1):
        for t in toks:
            rows.append([text_id, para_id, t.sent_id, t.tok_id, t.text, t.lemma, t.upos, t.feats, verbform(t.feats),
                         int(is_word(t.text)), int(is_punct(t.text)), int(t.upos in content_upos),
                         t.spacy_upos, t.spacy_lemma, t.spacy_feats, verbform(t.spacy_feats)])
    return rows


def tag_text(paragraphs: list[str], stanza_nlp, spacy_nlp, cfg: dict) -> tuple[list[list[Token]], list[str]]:
    """Tag one text and run the integrity checks; returns tokens and any split hyphenated words."""
    tagged = stanza_tag(stanza_nlp, paragraphs)
    split = []
    for p, toks in zip(paragraphs, tagged):
        check_characters(p, toks)
        split += split_hyphenated(p, toks)
    spacy_tag(spacy_nlp, paragraphs, tagged, cfg["spacy_batch_size"])
    return tagged, split


def read_tagged(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh, delimiter="\t", quoting=csv.QUOTE_NONE, quotechar=None))


def write_tagged(path: Path, rows: list[list]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t", lineterminator="\n", quoting=csv.QUOTE_NONE, quotechar=None)
        writer.writerow(COLUMNS)
        writer.writerows(rows)


def run(config_path: Path = DEFAULT_CONFIG) -> dict:
    config = load_config(config_path)
    cfg = config["tag"]
    check_versions(cfg)
    clean_dir, tagged_dir = repo_path(config, "clean_dir"), repo_path(config, "tagged_dir")
    tagged_dir.mkdir(parents=True, exist_ok=True)
    stanza_nlp = spacy_nlp = None
    metas = {}
    for text_id in config["corpus"]["texts"]:
        clean_path = clean_dir / f"{text_id}.txt"
        tsv_path, meta_path = tagged_dir / f"{text_id}.tsv", tagged_dir / f"{text_id}.meta.json"
        key = cache_key(clean_path, cfg)
        if tsv_path.exists() and meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta["cache_key"] == key:
                metas[text_id] = meta | {"cached": True}
                continue
        if stanza_nlp is None:
            stanza_nlp = load_stanza(cfg, repo_path(config, "stanza_model_dir"))
            spacy_nlp = load_spacy(cfg)
        paragraphs = read_paragraphs(clean_path)
        tagged, split = tag_text(paragraphs, stanza_nlp, spacy_nlp, cfg)
        if split:
            raise TaggingError(f"{text_id}: hyphenated words split by the tokenizer: {sorted(set(split))[:20]}")
        rows = token_rows(text_id, tagged, set(cfg["content_upos"]))
        write_tagged(tsv_path, rows)
        meta = {"text": text_id, "cache_key": key, "clean_sha256": file_sha256(clean_path),
                "paragraphs": len(paragraphs), "sentences": sum(len({t.sent_id for t in toks}) for toks in tagged),
                "tokens": len(rows), "word_tokens": sum(r[9] for r in rows),
                "versions": {k: cfg[k] for k in ("stanza_version", "stanza_resources_version",
                                                 "spacy_version", "spacy_model_version")}}
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        metas[text_id] = meta | {"cached": False}
    return metas


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--download", action="store_true", help="download the configured Stanza models first")
    args = parser.parse_args()
    if args.download:
        config = load_config(args.config)
        download_models(config["tag"], repo_path(config, "stanza_model_dir"))
    for text_id, meta in run(args.config).items():
        state = "cached" if meta["cached"] else "tagged"
        print(f"{text_id}: {state}; {meta['paragraphs']} paragraphs, {meta['sentences']} sentences, "
              f"{meta['tokens']} tokens ({meta['word_tokens']} words)")


if __name__ == "__main__":
    main()
