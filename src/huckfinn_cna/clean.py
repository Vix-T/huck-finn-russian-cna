"""Text cleaning: data/raw/Ru#.txt -> data/interim/clean/Ru#.txt plus Ru#_changes.tsv.

Rules a–k run in the order below; every parameter comes from config/pipeline.toml.
Raw files are only read. Line numbers in logs and in data/corrections/manual.tsv are
1-based line numbers of the raw file. One paragraph per line; text is never joined
across lines.

Usage:
    uv run python -m huckfinn_cna.clean [--config PATH]
"""

from __future__ import annotations

import argparse
import copy
import csv
import random
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import pymorphy3

from huckfinn_cna.config import DEFAULT_CONFIG, load_config, repo_path

CYR = "а-яёА-ЯЁ"
LAT = "A-Za-z"
CYR_RE = re.compile(f"[{CYR}]")
LAT_RE = re.compile(f"[{LAT}]")
MIXED_TOKEN_RE = re.compile(rf"[{CYR}{LAT}]+(?:-[{CYR}{LAT}]+)*")
# Word tokens for before/after counts; same definition as scripts/audit_data.py.
WORD_LETTERS = "а-яёА-ЯЁѣѢѳѲіІѵѴ"
WORD_RE = re.compile(rf"[{WORD_LETTERS}]+(?:-[{WORD_LETTERS}]+)*")
# Bare Cyrillic tokens for split-word repair: not part of a hyphenated or mixed token.
BARE_TOKEN_RE = re.compile(rf"(?<![\w-])[{CYR}]+(?![\w-])")

RULES = {
    "a": "read: utf-8-sig, CRLF to LF, NFC",
    "b": "front matter removed",
    "c": "editorial notes removed",
    "d": "whitespace",
    "e": "Latin look-alikes to Cyrillic",
    "f": "standalone token replacement",
    "g": "punctuation",
    "h": "broken hyphenation rejoined",
    "i": "split words joined",
    "i2": "split words joined by attestation (only if enabled)",
    "j": "ё to е",
    "k": "manual corrections",
}


@dataclass
class Change:
    rule: str
    line: int
    before: str
    after: str
    left: str = ""
    right: str = ""


@dataclass
class Line:
    number: int  # raw-file line number, 1-based
    text: str


@dataclass
class Result:
    lines: list[Line]
    changes: list[Change] = field(default_factory=list)
    removed_blocks: list[dict] = field(default_factory=list)
    brackets_kept: list[dict] = field(default_factory=list)
    split_candidates: list[dict] = field(default_factory=list)
    attested_joins: list[dict] = field(default_factory=list)


class CleaningError(Exception):
    pass


# --- Helpers ---------------------------------------------------------------


def sub_logged(pattern: re.Pattern, repl, line: Line, rule: str, changes: list[Change], ctx: int) -> None:
    """re.sub on line.text that logs every replacement; repl(match) may return None to skip."""
    text = line.text
    out, pos = [], 0
    for m in pattern.finditer(text):
        new = repl(m)
        if new is None or new == m.group(0):
            continue
        out += [text[pos:m.start()], new]
        pos = m.end()
        changes.append(Change(rule, line.number, m.group(0), new,
                              text[max(0, m.start() - ctx):m.start()], text[m.end():m.end() + ctx]))
    if pos:
        line.text = "".join(out) + text[pos:]


def alternation(words: list[str]) -> str:
    return "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))


# --- a. Reading ------------------------------------------------------------


def read_lines(raw: bytes, cfg: dict, changes: list[Change]) -> list[Line]:
    text = raw.decode(cfg["encoding"]).replace("\r\n", "\n").replace("\r", "\n")
    parts = text.split("\n")
    if parts and parts[-1] == "":
        parts.pop()
    lines = []
    for number, part in enumerate(parts, start=1):
        normal = unicodedata.normalize(cfg["unicode_form"], part)
        if normal != part:
            changes.append(Change("a", number, part, normal))
        lines.append(Line(number, normal))
    return lines


# --- b. Front matter -------------------------------------------------------


def find_block(lines: list[Line], first: str, last: str) -> tuple[int, int] | None:
    """Indices (start, end) of the block, None if absent; error if `first` matches more than once."""
    starts = [i for i, ln in enumerate(lines) if ln.text.strip().startswith(first)]
    if not starts:
        return None
    if len(starts) > 1:
        raise CleaningError(f"front matter start {first!r} matches {len(starts)} lines")
    end = next((j for j in range(starts[0], len(lines)) if lines[j].text.strip().startswith(last)), None)
    if end is None:
        raise CleaningError(f"front matter end {last!r} not found after {first!r}")
    return starts[0], end


def remove_front_matter(result: Result, blocks: list[dict], required: bool) -> None:
    for block in blocks:
        span = find_block(result.lines, block["first"], block["last"])
        if span is None:
            if required:
                raise CleaningError(f"front matter block {block['name']!r} ({block['first']!r}) not found")
            continue
        start, end = span
        cut = result.lines[start:end + 1]
        result.removed_blocks.append({
            "name": block["name"], "first_line": cut[0].number, "last_line": cut[-1].number,
            "first_text": cut[0].text.strip(), "last_text": cut[-1].text.strip(), "lines": len(cut),
        })
        result.changes += [Change("b", ln.number, ln.text, "") for ln in cut]
        del result.lines[start:end + 1]


# --- c. Editorial notes ----------------------------------------------------


def remove_editorial_notes(result: Result, cfg: dict, ctx: int) -> None:
    o, c = re.escape(cfg["open"]), re.escape(cfg["close"])
    pattern = re.compile(f"{o}[^{o}{c}]*{c}")
    for line in result.lines:
        for m in pattern.finditer(line.text):
            if cfg["marker"] not in m.group(0):
                result.brackets_kept.append({"line": line.number, "segment": m.group(0), "kind": "bracketed"})
        sub_logged(pattern, lambda m: "" if cfg["marker"] in m.group(0) else None, line, "c", result.changes, ctx)
        rest = pattern.sub("", line.text)
        if cfg["open"] in rest or cfg["close"] in rest:
            result.brackets_kept.append({"line": line.number, "segment": rest, "kind": "unmatched bracket"})
        if cfg["marker"] in line.text:
            result.brackets_kept.append({"line": line.number, "segment": line.text, "kind": "marker outside brackets"})


# --- d. Whitespace ---------------------------------------------------------

TAB_RE = re.compile("\t")
SPACE_RUN_RE = re.compile(" {2,}")
EDGE_SPACE_RE = re.compile(r"^\s+|\s+$")


def normalize_whitespace(line: Line, changes: list[Change], ctx: int) -> None:
    sub_logged(TAB_RE, lambda m: " ", line, "d", changes, ctx)
    sub_logged(SPACE_RUN_RE, lambda m: " ", line, "d", changes, ctx)
    sub_logged(EDGE_SPACE_RE, lambda m: "", line, "d", changes, ctx)


# --- e. Latin look-alikes --------------------------------------------------


def map_lookalikes(line: Line, mapping: dict[str, str], changes: list[Change], ctx: int) -> None:
    table = str.maketrans(mapping)

    def repl(m):
        token = m.group(0)
        if CYR_RE.search(token) and LAT_RE.search(token):
            return token.translate(table)
        return None

    sub_logged(MIXED_TOKEN_RE, repl, line, "e", changes, ctx)


def mixed_script_tokens(text: str) -> list[str]:
    return [t for t in MIXED_TOKEN_RE.findall(text) if CYR_RE.search(t) and LAT_RE.search(t)]


# --- f. Standalone tokens --------------------------------------------------


def replace_standalone(line: Line, mapping: dict[str, str], changes: list[Change], ctx: int) -> None:
    for token, replacement in mapping.items():
        pattern = re.compile(rf"(?<![\w-]){re.escape(token)}(?![\w-])")
        sub_logged(pattern, lambda m, r=replacement: r, line, "f", changes, ctx)


# --- g. Punctuation --------------------------------------------------------


def normalize_punctuation(line: Line, cfg: dict, changes: list[Change], ctx: int,
                          rejoin: list[re.Pattern] = ()) -> None:
    """Rule g. A spaced hyphen that a rule h pattern in `rejoin` would rejoin is left for rule h."""
    dash = cfg["dash"]

    def spaced_dash(m):
        at_start, at_end = m.start() == 0, m.end() == len(m.string)
        return (dash + " ") if at_start else (" " + dash) if at_end else f" {dash} "

    double = re.compile(rf" *{re.escape(cfg['double_hyphen'][0])}{{{len(cfg['double_hyphen'])},}} *")
    sub_logged(double, spaced_dash, line, "g", changes, ctx)

    spaced = re.compile(rf"(?<=\S) +(?:{alternation(cfg['spaced_dashes'])}) +(?=\S)")
    sub_logged(spaced, lambda m: None if m.group(0).strip() == "-" and is_rejoinable(m, rejoin) else f" {dash} ",
               line, "g", changes, ctx)

    quotes = re.compile(f"[{''.join(re.escape(q) for q in cfg['quote_chars'])}]")
    opening_after = set(cfg["opening_after"])

    def quote(m):
        s, i = m.string, m.start()
        prev = s[i - 1] if i else ""
        nxt = s[i + 1] if i + 1 < len(s) else ""
        if prev.isalpha() and nxt.isalpha():
            return None  # apostrophe inside a word
        opening = (not prev or prev.isspace() or prev in opening_after) and nxt and not nxt.isspace()
        return cfg["open_quote"] if opening else cfg["close_quote"]

    sub_logged(quotes, quote, line, "g", changes, ctx)

    sub_logged(re.compile(rf"\.{{{cfg['ellipsis_min_dots']},}}"), lambda m: cfg["ellipsis_to"], line, "g", changes, ctx)
    if cfg["comma_runs"]:
        sub_logged(re.compile(",{2,}"), lambda m: ",", line, "g", changes, ctx)


# --- h. Broken hyphenation -------------------------------------------------


HYPHEN_GAP = r"(?: +- *| *- +)"  # a hyphen with at least one space next to it
WORD = rf"[{CYR}]+(?:-[{CYR}]+)*"


def word_or_capitalized(words: list[str]) -> str:
    return alternation(sorted({form for w in words for form in (w, w.capitalize())}))


def rejoin_patterns(cfg: dict) -> list[re.Pattern]:
    """Rule h patterns; each has groups (left, right) that are rejoined as 'left-right'."""
    end = r"(?![\w-])"
    patterns = [
        rf"(?<![\w-])({WORD}){HYPHEN_GAP}({alternation(cfg['particles'])}){end}",
        rf"(?<![\w-])({word_or_capitalized([cfg['po_word']])}){HYPHEN_GAP}"
        rf"([{CYR}]+(?:{alternation(cfg['po_suffixes'])})){end}",
        rf"(?<![\w-])({word_or_capitalized(cfg['prefixes'])}){HYPHEN_GAP}({WORD}){end}",
    ]
    patterns += [rf"(?<![\w-])({word_or_capitalized([left])}){HYPHEN_GAP}({re.escape(right)}){end}"
                 for left, right in cfg["fixed_pairs"]]
    return [re.compile(p) for p in patterns]


def is_rejoinable(m: re.Match, patterns: list[re.Pattern]) -> bool:
    """True if the spaced hyphen in m sits inside a span that a rule h pattern would rejoin."""
    return any(hit.start() < m.start() and m.end() <= hit.end()
               for p in patterns for hit in p.finditer(m.string))


def rejoin_hyphenation(line: Line, cfg: dict, changes: list[Change], ctx: int) -> None:
    for pattern in rejoin_patterns(cfg):
        sub_logged(pattern, lambda m: f"{m.group(1)}-{m.group(2)}", line, "h", changes, ctx)


# --- i. Split words --------------------------------------------------------


@lru_cache(maxsize=None)
def _morph() -> pymorphy3.MorphAnalyzer:
    return pymorphy3.MorphAnalyzer()


@lru_cache(maxsize=None)
def is_known(word: str) -> bool:
    return _morph().word_is_known(word.lower())


def adjacent_pairs(text: str):
    """Pairs of bare Cyrillic tokens separated by exactly one space, with their joined form."""
    tokens = list(BARE_TOKEN_RE.finditer(text))
    for a, b in zip(tokens, tokens[1:]):
        if text[a.end():b.start()] == " ":
            yield a, b, a.group(0) + b.group(0)


def guard_reason(a: str, b: str, guards: dict) -> str | None:
    """Why the pair A B may never be joined (rules i and i2), or None."""
    if a.lower() in guards["never_join_after"]:
        return f"guard: A is {a.lower()!r}"
    if guards["block_if_second_capitalized"] and b[:1].isupper():
        return "guard: B capitalized"
    return None


BOTH_KNOWN = "both known"


def join_split_words(line: Line, changes: list[Change], candidates: list[dict], ctx: int, guards: dict) -> None:
    """Join A B -> AB if AB is a dictionary word, A or B is not, and no guard applies; repeat until none.

    Pairs not joined although AB is a dictionary word are recorded as candidates, with the reason
    (all three known, or the guard that blocked the join).
    """
    def joinable(a, b, ab):
        return (is_known(ab) and not (is_known(a.group(0)) and is_known(b.group(0)))
                and guard_reason(a.group(0), b.group(0), guards) is None)

    while True:
        text = line.text
        hit = next(((a, b, ab) for a, b, ab in adjacent_pairs(text) if joinable(a, b, ab)), None)
        if hit is None:
            break
        a, b, ab = hit
        changes.append(Change("i", line.number, f"{a.group(0)} {b.group(0)}", ab,
                              text[max(0, a.start() - ctx):a.start()], text[b.end():b.end() + ctx]))
        line.text = text[:a.start()] + ab + text[b.end():]
    for a, b, ab in adjacent_pairs(line.text):
        if is_known(ab):
            both = is_known(a.group(0)) and is_known(b.group(0))
            reason = BOTH_KNOWN if both else guard_reason(a.group(0), b.group(0), guards)
            candidates.append({"line": line.number, "a": a.group(0), "b": b.group(0), "joined": ab, "reason": reason})


# --- i2. Within-text attestation joins -------------------------------------


def normalize_form(word: str, yo: dict[str, str]) -> str:
    """Lowercase with ё folded, so comparisons do not depend on rule j having run."""
    return word.lower().translate(str.maketrans(yo))


def corpus_attestation(results: dict[str, Result], yo: dict[str, str]) -> dict:
    """Per text: single-token counts and the set of adjacent bare-token pairs (normalized)."""
    tokens, pairs = {}, {}
    for text_id, result in results.items():
        tokens[text_id] = Counter(normalize_form(t, yo) for ln in result.lines for t in BARE_TOKEN_RE.findall(ln.text))
        pairs[text_id] = {(normalize_form(a.group(0), yo), normalize_form(b.group(0), yo))
                          for ln in result.lines for a, b, _ in adjacent_pairs(ln.text)}
    return {"tokens": tokens, "pairs": pairs}


def attested_join_count(a: str, b: str, text_id: str, attestation: dict, cfg: dict) -> int | None:
    """Times AB is attested in the same text if all i2 conditions hold, else None."""
    yo = cfg["yo"]
    if guard_reason(a, b, cfg["split_words"]) is not None:
        return None
    if min(len(a), len(b)) < cfg["attested_joins"]["min_part_letters"]:
        return None
    pair = (normalize_form(a, yo), normalize_form(b, yo))
    if any(pair in pairs for other, pairs in attestation["pairs"].items() if other != text_id):
        return None
    attested = attestation["tokens"][text_id][normalize_form(a + b, yo)]
    return attested if attested >= cfg["attested_joins"]["min_attested"] else None


def apply_attested_joins(result: Result, text_id: str, attestation: dict, cfg: dict, ctx: int) -> None:
    """Rule i2: one left-to-right pass per line; a joined token is not paired again."""
    wide = cfg["attested_joins"]["applied_context_chars"]
    for line in result.lines:
        text = line.text
        out, pos = [], 0
        for a, b, ab in adjacent_pairs(text):
            if a.start() < pos:
                continue  # A was consumed by the previous join
            attested = attested_join_count(a.group(0), b.group(0), text_id, attestation, cfg)
            if attested is None:
                continue
            left, right = text[max(0, a.start() - ctx):a.start()], text[b.end():b.end() + ctx]
            result.changes.append(Change("i2", line.number, f"{a.group(0)} {b.group(0)}", ab, left, right))
            result.attested_joins.append({"line": line.number, "a": a.group(0), "b": b.group(0), "ab": ab,
                                          "attested": attested, "left": left, "right": right,
                                          "wide_left": text[max(0, a.start() - wide):a.start()],
                                          "wide_right": text[b.end():b.end() + wide]})
            out += [text[pos:a.start()], ab]
            pos = b.end()
        if pos:
            line.text = "".join(out) + text[pos:]


# --- j. ё ------------------------------------------------------------------


def replace_yo(line: Line, mapping: dict[str, str], changes: list[Change], ctx: int) -> None:
    pattern = re.compile(f"[{''.join(mapping)}]")
    sub_logged(pattern, lambda m: mapping[m.group(0)], line, "j", changes, ctx)


# --- k. Manual corrections -------------------------------------------------


def read_manual_corrections(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        return [row for row in csv.DictReader(fh, delimiter="\t") if any(v.strip() for v in row.values())]


def apply_manual_corrections(result: Result, corrections: list[dict], text_id: str, ctx: int) -> None:
    by_number = {ln.number: ln for ln in result.lines}
    for row in corrections:
        if row["text"] != text_id:
            continue
        line = by_number.get(int(row["line"]))
        count = line.text.count(row["before"]) if line else 0
        if count != 1:
            raise CleaningError(
                f"manual correction {text_id} line {row['line']} {row['before']!r}: matched {count} times, expected 1"
            )
        i = line.text.index(row["before"])
        result.changes.append(Change("k", line.number, row["before"], row["after"],
                                     line.text[max(0, i - ctx):i], line.text[i + len(row["before"]):][:ctx]))
        line.text = line.text.replace(row["before"], row["after"], 1)


# --- Pipeline --------------------------------------------------------------


def clean_lines(result: Result, cfg: dict) -> None:
    """Rules c–i on every line, in order."""
    ctx = cfg["log_context_chars"]
    rejoin = rejoin_patterns(cfg["hyphenation"])
    remove_editorial_notes(result, cfg["editorial_notes"], ctx)
    for line in result.lines:
        normalize_whitespace(line, result.changes, ctx)
        map_lookalikes(line, cfg["latin_lookalikes"], result.changes, ctx)
        replace_standalone(line, cfg["standalone_tokens"], result.changes, ctx)
        normalize_punctuation(line, cfg["punctuation"], result.changes, ctx, rejoin)
        rejoin_hyphenation(line, cfg["hyphenation"], result.changes, ctx)
        join_split_words(line, result.changes, result.split_candidates, ctx, cfg["split_words"])


def clean_stage1(raw: bytes, text_id: str, cfg: dict, front_matter_required: bool = True) -> Result:
    """Rules a–i for one text."""
    result = Result(lines=[])
    result.lines = read_lines(raw, cfg, result.changes)
    blocks = [b for b in cfg.get("front_matter", []) if b["text"] == text_id]
    remove_front_matter(result, blocks, front_matter_required)
    clean_lines(result, cfg)
    return result


def clean_stage2(result: Result, text_id: str, cfg: dict, corrections: list[dict],
                 attestation: dict | None, apply_i2: bool) -> None:
    """Rule i2 (only if apply_i2; needs corpus attestation), then rules j and k."""
    ctx = cfg["log_context_chars"]
    if apply_i2:
        if attestation is None:
            raise CleaningError("rule i2 needs corpus attestation; use clean_corpus")
        apply_attested_joins(result, text_id, attestation, cfg, ctx)
    for line in result.lines:
        replace_yo(line, cfg["yo"], result.changes, ctx)
    apply_manual_corrections(result, corrections, text_id, ctx)


def clean_text(raw: bytes, text_id: str, cfg: dict, corrections: list[dict] | None = None,
               front_matter_required: bool = True) -> Result:
    """Rules a–k except i2 for a single text (i2 needs the corpus; see clean_corpus).

    Rules b and k are one-shot: they match the raw text.
    """
    result = clean_stage1(raw, text_id, cfg, front_matter_required)
    clean_stage2(result, text_id, cfg, corrections or [], None, apply_i2=False)
    return result


def clean_corpus(stage1: dict[str, Result], cfg: dict, corrections: list[dict], apply_i2: bool) -> dict[str, Result]:
    """Stage 2 for every text, on deep copies of the stage-1 results."""
    attestation = corpus_attestation(stage1, cfg["yo"])
    results = {}
    for text_id, result in stage1.items():
        result = copy.deepcopy(result)
        clean_stage2(result, text_id, cfg, corrections, attestation, apply_i2)
        results[text_id] = result
    return results


def render(lines: list[Line]) -> str:
    return "".join(ln.text + "\n" for ln in lines)


def word_stats(text: str) -> dict:
    tokens = WORD_RE.findall(text)
    return {"tokens": len(tokens), "types": len({t.lower() for t in tokens})}


# --- Front/back matter proposals (report only) -----------------------------


def front_back_proposals(lines: list[Line], cfg: dict) -> list[dict]:
    non_empty = [ln for ln in lines if ln.text.strip()]
    n = cfg["scan_lines"]
    ends = [("front", ln) for ln in non_empty[:n]] + [("back", ln) for ln in non_empty[-n:]]
    proposals = []
    for where, ln in ends:
        text = ln.text.strip()
        if len(text) > cfg["max_chars"]:
            continue
        reasons = [f"keyword {k!r}" for k in cfg["keywords"] if k in text]
        if text.isupper():
            reasons.append("all caps")
        if reasons:
            proposals.append({"where": where, "line": ln.number, "text": text, "reasons": "; ".join(reasons)})
    return proposals


# --- Output ----------------------------------------------------------------


def write_tsv(path: Path, header: list[str], rows: list[list]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def write_outputs(text_id: str, result: Result, clean_dir: Path) -> None:
    (clean_dir / f"{text_id}.txt").write_text(render(result.lines), encoding="utf-8")
    write_tsv(clean_dir / f"{text_id}_changes.tsv", ["rule", "line", "before", "after", "left", "right"],
              [[c.rule, c.line, c.before, c.after, c.left, c.right] for c in result.changes])


def write_review(per_text: dict, review_dir: Path, cfg: dict) -> dict:
    """Review files for the project owner; returns the sampled join counts."""
    review_dir.mkdir(parents=True, exist_ok=True)
    sw = cfg["split_words"]
    rng = random.Random(sw["seed"])
    sample_rows, candidate_rows, mixed_rows, bracket_rows, proposal_rows = [], [], [], [], []
    sampled = {}
    for text_id, info in per_text.items():
        joins = [c for c in info["result"].changes if c.rule == "i"]
        sample = rng.sample(joins, min(sw["sample_size"], len(joins)))
        sampled[text_id] = len(sample)
        sample_rows += [[text_id, c.line, c.before, c.after, c.left, c.right] for c in sorted(sample, key=lambda c: c.line)]
        pairs = Counter((c["a"].lower(), c["b"].lower(), c["joined"].lower(), c["reason"])
                        for c in info["result"].split_candidates)
        candidate_rows += [[text_id, a, b, j, reason, n] for (a, b, j, reason), n in pairs.most_common()]
        mixed = Counter((ln.number, tok) for ln in info["result"].lines for tok in mixed_script_tokens(ln.text))
        mixed_rows += [[text_id, line, tok] for (line, tok) in sorted(mixed)]
        bracket_rows += [[text_id, b["line"], b["kind"], b["segment"]] for b in info["result"].brackets_kept]
        proposal_rows += [[text_id, p["where"], p["line"], p["reasons"], p["text"]] for p in info["proposals"]]
    write_tsv(review_dir / "split_joins_sample.tsv", ["text", "line", "before", "after", "left", "right"], sample_rows)
    write_tsv(review_dir / "split_candidates_not_joined.tsv", ["text", "a", "b", "would_be", "reason", "count"],
              candidate_rows)
    write_attested_review(per_text, review_dir, cfg["attested_joins"])
    write_tsv(review_dir / "mixed_script_tokens.tsv", ["text", "line", "token"], mixed_rows)
    write_tsv(review_dir / "brackets_not_removed.tsv", ["text", "line", "kind", "segment"], bracket_rows)
    write_tsv(review_dir / "front_back_proposals.tsv", ["text", "where", "line", "reasons", "text_line"], proposal_rows)
    return sampled


def attested_pair_counts(joins: list[dict]) -> list[tuple[tuple[str, str, str], int, int]]:
    """((a, b, ab) lowercased, joins, attested count) per distinct pair, most frequent first."""
    counts = Counter((j["a"].lower(), j["b"].lower(), j["ab"].lower()) for j in joins)
    attested = {(j["a"].lower(), j["b"].lower(), j["ab"].lower()): j["attested"] for j in joins}
    return [(pair, n, attested[pair]) for pair, n in counts.most_common()]


def write_attested_review(per_text: dict, review_dir: Path, cfg: dict) -> None:
    """Rule i2 preview: every would-be join, per-pair counts, and a seeded sample for one text."""
    preview, pair_rows = [], []
    for text_id, info in per_text.items():
        joins = info["projection"].attested_joins if info["projection"] else []
        preview += [[text_id, j["line"], j["a"], j["b"], j["ab"], j["attested"], f"{j['left']}[{j['a']} {j['b']}]{j['right']}"]
                    for j in joins]
        pair_rows += [[text_id, a, b, ab, n, att] for (a, b, ab), n, att in attested_pair_counts(joins)]
    write_tsv(review_dir / "i2_preview.tsv", ["text", "line", "A", "B", "AB", "attested_in_text", "context"], preview)
    write_tsv(review_dir / "i2_pairs.tsv", ["text", "A", "B", "AB", "joins", "attested_in_text"], pair_rows)
    target = cfg["sample_text"]
    rows = [r for r in preview if r[0] == target]
    sample = random.Random(cfg["seed"]).sample(rows, min(cfg["sample_size"], len(rows)))
    write_tsv(review_dir / f"i2_sample_{target}.tsv", ["text", "line", "A", "B", "AB", "attested_in_text", "context"],
              sorted(sample, key=lambda r: r[1]))
    applied_path = review_dir / f"i2_applied_sample_{target}.tsv"
    if cfg["apply"]:
        write_tsv(applied_path, ["text", "line", "A", "B", "AB", "attested_in_text", "context"],
                  [[target, j["line"], j["a"], j["b"], j["ab"], j["attested"],
                    f"{j['wide_left']}[{j['a']} {j['b']}]{j['wide_right']}"]
                   for j in applied_join_sample(per_text[target]["result"].attested_joins, cfg)])
    else:
        applied_path.unlink(missing_ok=True)


def applied_join_sample(joins: list[dict], cfg: dict) -> list[dict]:
    """Seeded sample of applied i2 joins, in text order."""
    sample = random.Random(cfg["applied_seed"]).sample(joins, min(cfg["applied_sample_size"], len(joins)))
    return sorted(sample, key=lambda j: j["line"])


def excerpt(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n] + "…"


def render_report(per_text: dict, config: dict, sampled: dict) -> str:
    cfg = config["clean"]
    labels = config["corpus"]["labels"]
    ids = list(per_text)
    n = cfg["report_excerpt_chars"]
    L = ["# Cleaning report", "",
         "Generated by `uv run python -m huckfinn_cna.clean`. Counts only; the cleaned texts, change logs and",
         "review files are in `data/interim/clean/` (not committed). Rules and parameters: `config/pipeline.toml`;",
         "rationale: `docs/decisions.md`.", ""]

    L += ["## Changes per rule", "", "| Rule | " + " | ".join(ids) + " |", "|---|" + "---|" * len(ids)]
    for rule, desc in RULES.items():
        counts = [sum(1 for c in per_text[t]["result"].changes if c.rule == rule) for t in ids]
        L.append(f"| {rule}. {desc} | " + " | ".join(str(c) for c in counts) + " |")
    totals = [len(per_text[t]["result"].changes) for t in ids]
    L.append("| **total** | " + " | ".join(str(c) for c in totals) + " |")

    L += ["", "## Word tokens and types, before and after", "",
          "Tokens: Cyrillic letter runs, internal hyphens join parts (same definition as the data audit).",
          "Types are lowercased. *Before* is the raw text after BOM strip and newline normalization.", "",
          "| Text | Label | Tokens before | Tokens after | Δ tokens | Types before | Types after | Δ types | Δ types % |",
          "|---|---|---|---|---|---|---|---|---|"]
    for t in ids:
        b, a = per_text[t]["before"], per_text[t]["after"]
        dt = a["types"] - b["types"]
        L.append(f"| {t} | {labels[t]} | {b['tokens']} | {a['tokens']} | {a['tokens'] - b['tokens']:+d} | "
                 f"{b['types']} | {a['types']} | {dt:+d} | {100 * dt / b['types']:+.1f}% |")

    L += ["", "## Front matter removed (rule b)", ""]
    for t in ids:
        for blk in per_text[t]["result"].removed_blocks:
            L += [f"- **{t}, {blk['name']}**: raw lines {blk['first_line']}–{blk['last_line']} ({blk['lines']} lines)",
                  f"  - first: `{excerpt(blk['first_text'], n)}`",
                  f"  - last: `{excerpt(blk['last_text'], n)}`"]
    no_block = [t for t in ids if not per_text[t]["result"].removed_blocks]
    L += [f"- Not present (nothing removed): {', '.join(no_block)}.",
          "- Ru1 contains the Notice only; it has no Explanatory note."]

    L += ["", "## Proposed front/back trims (not applied)", "",
          f"Lines among the first/last {cfg['front_back_proposals']['scan_lines']} non-empty lines of at most "
          f"{cfg['front_back_proposals']['max_chars']} characters",
          "that are all caps or contain a keyword (see config). Full list: "
          "`data/interim/clean/review/front_back_proposals.tsv`.", "",
          "| Text | Where | Raw line | Line | Reasons |", "|---|---|---|---|---|"]
    for t in ids:
        for p in per_text[t]["proposals"]:
            L.append(f"| {t} | {p['where']} | {p['line']} | {excerpt(p['text'], cfg['front_back_proposals']['max_chars'])} | {p['reasons']} |")

    L += ["", "## Review items (counts)", "",
          "| Item | " + " | ".join(ids) + " |", "|---|" + "---|" * len(ids)]
    rows = {
        "split-word joins sampled for review": [sampled.get(t, 0) for t in ids],
        "split candidates not joined, all known (occurrences)": [len(both_known(per_text[t])) for t in ids],
        "split candidates not joined, all known (distinct pairs)": [
            len({(c["a"].lower(), c["b"].lower()) for c in both_known(per_text[t])}) for t in ids],
        "split joins blocked by a guard (occurrences)": [
            sum(1 for c in per_text[t]["result"].split_candidates if c["reason"] != BOTH_KNOWN) for t in ids],
        "mixed-script tokens left": [sum(len(mixed_script_tokens(ln.text)) for ln in per_text[t]["result"].lines)
                                     for t in ids],
        "bracketed segments not removed": [sum(1 for b in per_text[t]["result"].brackets_kept if b["kind"] == "bracketed")
                                           for t in ids],
        "unmatched brackets / marker outside brackets": [
            sum(1 for b in per_text[t]["result"].brackets_kept if b["kind"] != "bracketed") for t in ids],
        "half-spaced hyphens left (`word- word`)": [per_text[t]["residual_half_hyphens"] for t in ids],
    }
    L += [f"| {k} | " + " | ".join(str(v) for v in vals) + " |" for k, vals in rows.items()]
    L += ["", f"Split-word review sample: {cfg['split_words']['sample_size']} joins per text, "
              f"seed {cfg['split_words']['seed']} (`random.Random`, texts in corpus order)."]

    per = cfg["candidate_rate_per"]
    L += ["", "## Split-word candidate rate", "",
          f"Candidates not joined (A, B and AB all dictionary words) per {per:,} word tokens of the cleaned text.", "",
          f"| Text | Word tokens | Candidates | per {per:,} tokens |", "|---|---|---|---|"]
    for t in ids:
        tokens, cands = per_text[t]["after"]["tokens"], len(both_known(per_text[t]))
        L.append(f"| {t} | {tokens} | {cands} | {per * cands / tokens:.1f} |")

    att = cfg["attested_joins"]
    conditions = (f"AB attested ≥ {att['min_attested']} times as one token in the same text, pair «A B» absent "
                  f"from the other four texts, rule i guards, A and B each ≥ {att['min_part_letters']} letters")
    if att["apply"]:
        L += ["", "## Rule i2 (attestation joins): effect", "",
              f"Rule i2 is **ON** (`clean.attested_joins.apply`) and applied to the cleaned texts ({conditions}).",
              "*Types (i2 off)* is the same pipeline without i2; *Types (i2 on)* matches the table above.", ""]
    else:
        L += ["", "## Rule i2 (attestation joins): projected effect", "",
              f"Rule i2 is **OFF** (`clean.attested_joins.apply`). The projection runs the full pipeline with i2",
              f"applied ({conditions}); it is not written as cleaned text.", ""]
    if any(per_text[t]["projection_error"] for t in ids):
        L.append(f"**Projection failed:** {per_text[ids[0]]['projection_error']}")
    else:
        L += [f"| Text | {'Joins' if att['apply'] else 'Would-be joins'} | Distinct pairs | Types (i2 off) | "
              "Types (i2 on) | Δ types | Δ types % |",
              "|---|---|---|---|---|---|---|"]
        for t in ids:
            joins = per_text[t]["projection"].attested_joins
            off, on = per_text[t]["without_i2"]["types"], per_text[t]["projected_after"]["types"]
            L.append(f"| {t} | {len(joins)} | {len(attested_pair_counts(joins))} | {off} | {on} | {on - off:+d} | "
                     f"{100 * (on - off) / off:+.1f}% |")
        target = att["sample_text"]
        L += ["", f"Review: `i2_preview.tsv` (all would-be joins), `i2_pairs.tsv` (joins per distinct pair), "
                  f"`i2_sample_{target}.tsv` ({att['sample_size']} joins, seed {att['seed']})"
                  + (f", `i2_applied_sample_{target}.tsv` ({att['applied_sample_size']} applied joins, "
                     f"seed {att['applied_seed']}, {att['applied_context_chars']} characters of context)."
                     if att["apply"] else "."), "",
              f"### {target}: {att['top_n']} most frequent {'' if att['apply'] else 'would-be '}i2 joins", "",
              "| A B | AB | Joins | AB attested in text |", "|---|---|---|---|"]
        for (a, b, ab), n, attested in attested_pair_counts(per_text[target]["projection"].attested_joins)[:att["top_n"]]:
            L.append(f"| {a} {b} | {ab} | {n} | {attested} |")
    return "\n".join(L) + "\n"


def both_known(info: dict) -> list[dict]:
    return [c for c in info["result"].split_candidates if c["reason"] == BOTH_KNOWN]


HALF_HYPHEN_RE = re.compile(rf"[{CYR}](?:- +| +-)[{CYR}]")


def run(config_path: Path = DEFAULT_CONFIG, clean_dir: Path | None = None, review_dir: Path | None = None,
        report_path: Path | None = None) -> dict:
    config = load_config(config_path)
    cfg = config["clean"]
    raw_dir = repo_path(config, "raw_dir")
    clean_dir = clean_dir or repo_path(config, "clean_dir")
    review_dir = review_dir or repo_path(config, "review_dir")
    report_path = report_path or repo_path(config, "cleaning_report")
    corrections = read_manual_corrections(repo_path(config, "manual_corrections"))
    clean_dir.mkdir(parents=True, exist_ok=True)

    raws = {t: (raw_dir / f"{t}.txt").read_bytes() for t in config["corpus"]["texts"]}
    stage1 = {t: clean_stage1(raw, t, cfg) for t, raw in raws.items()}
    results = clean_corpus(stage1, cfg, corrections, apply_i2=cfg["attested_joins"]["apply"])
    # Projection with rule i2 applied, for the report and review files only; never written as cleaned text.
    try:
        projection, projection_error = clean_corpus(stage1, cfg, corrections, apply_i2=True), None
    except CleaningError as err:
        projection, projection_error = None, str(err)
    # Baseline without rule i2, for the report's off/on comparison.
    without_i2 = clean_corpus(stage1, cfg, corrections, apply_i2=False) if cfg["attested_joins"]["apply"] else results

    per_text = {}
    for text_id, result in results.items():
        before_text = raws[text_id].decode(cfg["encoding"]).replace("\r\n", "\n").replace("\r", "\n")
        after_text = render(result.lines)
        per_text[text_id] = {
            "result": result,
            "before": word_stats(before_text),
            "after": word_stats(after_text),
            "without_i2": word_stats(render(without_i2[text_id].lines)),
            "proposals": front_back_proposals(result.lines, cfg["front_back_proposals"]),
            "residual_half_hyphens": len(HALF_HYPHEN_RE.findall(after_text)),
            "projection": projection[text_id] if projection else None,
            "projected_after": word_stats(render(projection[text_id].lines)) if projection else None,
            "projection_error": projection_error,
        }
        write_outputs(text_id, result, clean_dir)

    sampled = write_review(per_text, review_dir, cfg)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_report(per_text, config, sampled), encoding="utf-8")
    return per_text


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    per_text = run(args.config)
    for text_id, info in per_text.items():
        print(f"{text_id}: {len(info['result'].changes)} changes, "
              f"types {info['before']['types']} -> {info['after']['types']}")


if __name__ == "__main__":
    main()
