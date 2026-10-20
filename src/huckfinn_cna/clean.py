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


def normalize_punctuation(line: Line, cfg: dict, changes: list[Change], ctx: int) -> None:
    dash = cfg["dash"]

    def spaced_dash(m):
        at_start, at_end = m.start() == 0, m.end() == len(m.string)
        return (dash + " ") if at_start else (" " + dash) if at_end else f" {dash} "

    double = re.compile(rf" *{re.escape(cfg['double_hyphen'][0])}{{{len(cfg['double_hyphen'])},}} *")
    sub_logged(double, spaced_dash, line, "g", changes, ctx)

    spaced = re.compile(rf"(?<=\S) +(?:{alternation(cfg['spaced_dashes'])}) +(?=\S)")
    sub_logged(spaced, lambda m: f" {dash} ", line, "g", changes, ctx)

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


def rejoin_hyphenation(line: Line, cfg: dict, changes: list[Change], ctx: int) -> None:
    gap = r"(?: +- *| *- +)"  # at least one space next to the hyphen
    particle = re.compile(rf"(?<![\w-])([{CYR}]+(?:-[{CYR}]+)*){gap}({alternation(cfg['particles'])})(?![\w-])")
    sub_logged(particle, lambda m: f"{m.group(1)}-{m.group(2)}", line, "h", changes, ctx)
    po = re.escape(cfg["po_word"])
    po_pattern = re.compile(
        rf"(?<![\w-])({po}|{po.capitalize()}){gap}([{CYR}]+(?:{alternation(cfg['po_suffixes'])}))(?![\w-])"
    )
    sub_logged(po_pattern, lambda m: f"{m.group(1)}-{m.group(2)}", line, "h", changes, ctx)


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


def join_split_words(line: Line, changes: list[Change], candidates: list[dict], ctx: int) -> None:
    """Join A B -> AB if AB is a dictionary word and A or B is not, repeated until nothing joins.

    Pairs where A, B and AB are all known are not joined; they are recorded as candidates.
    """
    while True:
        text = line.text
        hit = next(((a, b, ab) for a, b, ab in adjacent_pairs(text)
                    if is_known(ab) and not (is_known(a.group(0)) and is_known(b.group(0)))), None)
        if hit is None:
            break
        a, b, ab = hit
        changes.append(Change("i", line.number, f"{a.group(0)} {b.group(0)}", ab,
                              text[max(0, a.start() - ctx):a.start()], text[b.end():b.end() + ctx]))
        line.text = text[:a.start()] + ab + text[b.end():]
    candidates += [{"line": line.number, "a": a.group(0), "b": b.group(0), "joined": ab}
                   for a, b, ab in adjacent_pairs(line.text) if is_known(ab)]


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
    """Rules c–j on every line, in order."""
    ctx = cfg["log_context_chars"]
    remove_editorial_notes(result, cfg["editorial_notes"], ctx)
    for line in result.lines:
        normalize_whitespace(line, result.changes, ctx)
        map_lookalikes(line, cfg["latin_lookalikes"], result.changes, ctx)
        replace_standalone(line, cfg["standalone_tokens"], result.changes, ctx)
        normalize_punctuation(line, cfg["punctuation"], result.changes, ctx)
        rejoin_hyphenation(line, cfg["hyphenation"], result.changes, ctx)
        join_split_words(line, result.changes, result.split_candidates, ctx)
        replace_yo(line, cfg["yo"], result.changes, ctx)


def clean_text(raw: bytes, text_id: str, cfg: dict, corrections: list[dict] | None = None,
               front_matter_required: bool = True) -> Result:
    """All rules a–k for one text. Rules b and k are one-shot (they match the raw text)."""
    result = Result(lines=[])
    result.lines = read_lines(raw, cfg, result.changes)
    blocks = [b for b in cfg.get("front_matter", []) if b["text"] == text_id]
    remove_front_matter(result, blocks, front_matter_required)
    clean_lines(result, cfg)
    apply_manual_corrections(result, corrections or [], text_id, cfg["log_context_chars"])
    return result


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
        pairs = Counter((c["a"].lower(), c["b"].lower(), c["joined"].lower()) for c in info["result"].split_candidates)
        candidate_rows += [[text_id, a, b, j, n] for (a, b, j), n in pairs.most_common()]
        mixed = Counter((ln.number, tok) for ln in info["result"].lines for tok in mixed_script_tokens(ln.text))
        mixed_rows += [[text_id, line, tok] for (line, tok) in sorted(mixed)]
        bracket_rows += [[text_id, b["line"], b["kind"], b["segment"]] for b in info["result"].brackets_kept]
        proposal_rows += [[text_id, p["where"], p["line"], p["reasons"], p["text"]] for p in info["proposals"]]
    write_tsv(review_dir / "split_joins_sample.tsv", ["text", "line", "before", "after", "left", "right"], sample_rows)
    write_tsv(review_dir / "split_candidates_not_joined.tsv", ["text", "a", "b", "would_be", "count"], candidate_rows)
    write_tsv(review_dir / "mixed_script_tokens.tsv", ["text", "line", "token"], mixed_rows)
    write_tsv(review_dir / "brackets_not_removed.tsv", ["text", "line", "kind", "segment"], bracket_rows)
    write_tsv(review_dir / "front_back_proposals.tsv", ["text", "where", "line", "reasons", "text_line"], proposal_rows)
    return sampled


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
        "split candidates not joined (occurrences)": [len(per_text[t]["result"].split_candidates) for t in ids],
        "split candidates not joined (distinct pairs)": [
            len({(c["a"].lower(), c["b"].lower()) for c in per_text[t]["result"].split_candidates}) for t in ids],
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
    return "\n".join(L) + "\n"


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

    per_text = {}
    for text_id in config["corpus"]["texts"]:
        raw = (raw_dir / f"{text_id}.txt").read_bytes()
        result = clean_text(raw, text_id, cfg, corrections)
        before_text = raw.decode(cfg["encoding"]).replace("\r\n", "\n").replace("\r", "\n")
        after_text = render(result.lines)
        per_text[text_id] = {
            "result": result,
            "before": word_stats(before_text),
            "after": word_stats(after_text),
            "proposals": front_back_proposals(result.lines, cfg["front_back_proposals"]),
            "residual_half_hyphens": len(HALF_HYPHEN_RE.findall(after_text)),
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
