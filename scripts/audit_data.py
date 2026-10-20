"""Data audit for the five Russian translations and the old mfinder outputs.

Reads data/raw/Ru1.txt .. Ru5.txt (never writes to them) and the old repo
(read-only, including read-only `git show` of committed snapshots), and writes
docs/audit/data_audit.md and docs/audit/data_audit.json.

Usage:
    .venv/bin/python scripts/audit_data.py
"""

from __future__ import annotations

import hashlib
import json
import re
import statistics
import subprocess
import unicodedata
from collections import Counter
from pathlib import Path

from razdel import sentenize

# --- Settings (audit-only; a project config file does not exist yet) -------

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = REPO_ROOT / "data" / "raw"
AUDIT_DIR = REPO_ROOT / "docs" / "audit"
OLD_REPO = Path("/Users/vixtalbot/Code/nlp-descriptive-russian-translations")

TEXTS = [
    ("Ru1.txt", "Ru-1911-Engelgardt"),
    ("Ru2.txt", "Ru-1926-Anon"),
    ("Ru3.txt", "Ru-1933-Chukovsky-(Ed.)"),
    ("Ru4.txt", "Ru-1949-Braude"),
    ("Ru5.txt", "Ru-1960-Daruzes"),
]

READ_ENCODING = "utf-8-sig"  # strips the BOM if present
MAX_EXAMPLES = 5
EXAMPLE_CONTEXT_CHARS = 60  # characters shown either side of a match
TOP_UNUSUAL = 40
FRONT_BACK_LINES = 15
FRONT_BACK_MAX_CHARS = 200
PER_N_WORDS = 1000

MFINDER_PATTERNS = ("*_OUT.txt", "*_MEMBERS.txt", "*_mfinder_input.txt")
ZSCORE_SENTINEL = 888888.0
ZSCORE_SUSPECT_ABOVE = 1000.0
MEMBERS_CAP = 1000

# --- Character classes -----------------------------------------------------

LETTERS = "а-яёА-ЯЁѣѢѳѲіІѵѴ"
WORD_RE = re.compile(rf"[{LETTERS}]+(?:-[{LETTERS}]+)*")
CONSONANTS = set("бвгджзйклмнпрстфхцчшщ")

PREREFORM_LETTERS = {"ѣ": "ѣѢ", "ѳ": "ѳѲ", "і": "іІ", "ѵ": "ѵѴ"}
PREREFORM_ENDINGS = ("аго", "яго", "ыя", "ія")

PUNCTUATION = ["—", "–", "-", "«", "»", '"', "„", "“", "”", "'", "…"]
DIALOGUE_DASHES = ("—", "–", "-")
BASIC_PUNCTUATION = set(".,;:!?()") | set(PUNCTUATION)
ALLOWED_WHITESPACE = {" ", "\n"}


# --- Reading ---------------------------------------------------------------


def decode_text(raw: bytes) -> str:
    """Decode with utf-8-sig and normalize CRLF/CR to LF, in memory only."""
    text = raw.decode(READ_ENCODING)
    return text.replace("\r\n", "\n").replace("\r", "\n")


def split_lines(text: str) -> list[str]:
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


# --- File basics -----------------------------------------------------------


def line_ending_counts(raw: bytes) -> dict[str, int]:
    crlf = raw.count(b"\r\n")
    return {
        "CRLF": crlf,
        "LF": raw.count(b"\n") - crlf,
        "CR": raw.count(b"\r") - crlf,
    }


def line_ending_style(counts: dict[str, int]) -> str:
    present = [k for k, v in counts.items() if v]
    if not present:
        return "none"
    return present[0] if len(present) == 1 else "mixed (" + ", ".join(present) + ")"


def is_valid_utf8(raw: bytes) -> bool:
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def file_basics(raw: bytes, text: str) -> dict:
    endings = line_ending_counts(raw)
    return {
        "size_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "valid_utf8": is_valid_utf8(raw),
        "bom": raw.startswith(b"\xef\xbb\xbf"),
        "line_endings": endings,
        "line_ending_style": line_ending_style(endings),
        "line_count": len(split_lines(text)),
    }


# --- Lengths ---------------------------------------------------------------


def word_tokens(text: str) -> list[str]:
    return WORD_RE.findall(text)


def lengths(text: str) -> dict:
    lines = split_lines(text)
    non_empty = [ln for ln in lines if ln.strip()]
    line_lengths = [len(ln) for ln in non_empty]
    words = word_tokens(text)
    return {
        "characters": len(text),
        "paragraphs_non_empty_lines": len(non_empty),
        "blank_lines": len(lines) - len(non_empty),
        "line_length_min": min(line_lengths, default=0),
        "line_length_median": statistics.median(line_lengths) if line_lengths else 0,
        "line_length_max": max(line_lengths, default=0),
        "sentences": sum(1 for _ in sentenize(text)),
        "word_tokens": len(words),
        "word_types_lower": len({w.lower() for w in words}),
    }


# --- Pre-reform spelling ---------------------------------------------------


def snippet(line: str, start: int, end: int) -> str:
    lo = max(0, start - EXAMPLE_CONTEXT_CHARS)
    hi = min(len(line), end + EXAMPLE_CONTEXT_CHARS)
    return ("…" if lo else "") + line[lo:hi] + ("…" if hi < len(line) else "")


def find_examples(lines: list[str], pattern: re.Pattern) -> list[dict]:
    """Up to MAX_EXAMPLES distinct lines matching pattern, with line number and context."""
    examples = []
    for number, line in enumerate(lines, start=1):
        match = pattern.search(line)
        if match:
            examples.append({"line": number, "text": snippet(line, *match.span())})
            if len(examples) == MAX_EXAMPLES:
                break
    return examples


def hard_sign_after_consonant(token: str) -> int:
    """Number of hyphen-separated parts ending in consonant + ъ (e.g. 'какъ-то' -> 1)."""
    parts = token.lower().split("-")
    return sum(1 for p in parts if len(p) >= 2 and p[-1] == "ъ" and p[-2] in CONSONANTS)


def ending_count(tokens: list[str], ending: str) -> int:
    return sum(1 for t in tokens if any(p.endswith(ending) for p in t.lower().split("-")))


def prereform(text: str) -> dict:
    lines = split_lines(text)
    tokens = word_tokens(text)
    features = {}
    for name, chars in PREREFORM_LETTERS.items():
        features[f"letter_{name}"] = {
            "count": sum(text.count(c) for c in chars),
            "pattern": re.compile(f"[{chars}]"),
        }
    features["final_hard_sign"] = {
        "count": sum(hard_sign_after_consonant(t) for t in tokens),
        "pattern": re.compile(
            rf"(?<![{LETTERS}])[{LETTERS}]*[бвгджзйклмнпрстфхцчшщБВГДЖЗЙКЛМНПРСТФХЦЧШЩ][ъЪ](?![{LETTERS}])"
        ),
    }
    for ending in PREREFORM_ENDINGS:
        features[f"ending_-{ending}"] = {
            "count": ending_count(tokens, ending),
            "pattern": re.compile(rf"[{LETTERS}]{ending}(?![{LETTERS}])", re.IGNORECASE),
        }
    return {
        name: {
            "count": f["count"],
            "examples": find_examples(lines, f["pattern"]) if f["count"] else [],
        }
        for name, f in features.items()
    }


# --- ё usage ---------------------------------------------------------------


def yo_usage(text: str) -> dict:
    tokens = [t.lower() for t in word_tokens(text)]
    yo = text.count("ё") + text.count("Ё")
    return {
        "yo_count": yo,
        "yo_per_1000_words": round(yo * PER_N_WORDS / len(tokens), 3) if tokens else 0.0,
        "vsyo": tokens.count("всё"),
        "vse": tokens.count("все"),
    }


# --- Punctuation -----------------------------------------------------------


def punctuation(text: str) -> dict:
    lines = split_lines(text)
    dialogue = {d: sum(1 for ln in lines if ln.lstrip().startswith(d)) for d in DIALOGUE_DASHES}
    return {
        "counts": {p: text.count(p) for p in PUNCTUATION},
        "lines_starting_with_dash": dialogue,
        "lines_starting_with_dash_total": sum(dialogue.values()),
    }


# --- Unusual characters ----------------------------------------------------


def is_cyrillic(ch: str) -> bool:
    return "Ѐ" <= ch <= "ӿ"


def is_usual(ch: str) -> bool:
    return is_cyrillic(ch) or ch in BASIC_PUNCTUATION or ch in ALLOWED_WHITESPACE


def unusual_characters(text: str) -> dict:
    counts = Counter(ch for ch in text if not is_usual(ch))
    return {
        "distinct": len(counts),
        "total": sum(counts.values()),
        "top": [
            {
                "char": ch,
                "codepoint": f"U+{ord(ch):04X}",
                "name": unicodedata.name(ch, "<unnamed>"),
                "count": n,
            }
            for ch, n in counts.most_common(TOP_UNUSUAL)
        ],
    }


# --- Front and back matter -------------------------------------------------


def truncate(line: str) -> str:
    return line if len(line) <= FRONT_BACK_MAX_CHARS else line[:FRONT_BACK_MAX_CHARS] + "…"


def front_back(text: str) -> dict:
    numbered = [(i, ln) for i, ln in enumerate(split_lines(text), start=1) if ln.strip()]
    pick = lambda rows: [{"line": i, "text": truncate(ln)} for i, ln in rows]
    return {
        "first": pick(numbered[:FRONT_BACK_LINES]),
        "last": pick(numbered[-FRONT_BACK_LINES:]),
    }


# --- Per-text audit --------------------------------------------------------


def audit_text(path: Path, label: str) -> dict:
    raw = path.read_bytes()
    text = decode_text(raw)
    return {
        "file": path.name,
        "label": label,
        "basics": file_basics(raw, text),
        "lengths": lengths(text),
        "prereform": prereform(text),
        "yo": yo_usage(text),
        "punctuation": punctuation(text),
        "unusual": unusual_characters(text),
        "front_back": front_back(text),
    }


# --- Old mfinder outputs (read-only) ---------------------------------------


def parse_edges(content: str) -> list[tuple[str, ...]]:
    return [tuple(ln.split()) for ln in content.splitlines() if ln.strip()]


def audit_mfinder_input(content: str) -> dict:
    edges = parse_edges(content)
    pairs = Counter((e[0], e[1]) for e in edges if len(e) >= 2)
    weights = [e[2] for e in edges if len(e) >= 3]
    duplicates = {f"{s} {t}": n for (s, t), n in pairs.items() if n > 1}
    numeric = [float(w) for w in weights]
    return {
        "edges": len(edges),
        "malformed_rows": sum(1 for e in edges if len(e) != 3),
        "duplicate_pairs": len(duplicates),
        "duplicate_examples": dict(list(duplicates.items())[:MAX_EXAMPLES]),
        "self_loops": sum(1 for s, t in pairs if s == t),
        "weights_all_one": bool(numeric) and all(w == 1 for w in numeric),
        "weights_not_one": sum(1 for w in numeric if w != 1),
        "weight_max": max(numeric, default=None),
        "nodes": len({n for pair in pairs for n in pair}),
    }


def parse_out_zscores(content: str) -> dict[int, float]:
    """Motif ID -> Z-score, same layout as the old 21z-score_heatmap script expects."""
    data = {}
    in_block = False
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        if "Full list of subgraphs size" in line:
            in_block = True
            continue
        if in_block and line[0].isdigit():
            parts = line.split()
            if len(parts) >= 4:
                try:
                    data[int(parts[0])] = float(parts[3])
                except ValueError:
                    continue
    return data


def flag_zscores(zscores: dict[int, float]) -> list[dict]:
    return [
        {"motif": m, "z": z, "sentinel": z == ZSCORE_SENTINEL}
        for m, z in sorted(zscores.items())
        if z == ZSCORE_SENTINEL or abs(z) > ZSCORE_SUSPECT_ABOVE
    ]


def parse_members(content: str) -> dict[str, int]:
    """Motif ID -> number of member rows, same layout as the old 19parse script expects."""
    counts: Counter = Counter()
    current = None
    started = False
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("subgraph id ="):
            parts = line.split()
            current = parts[3] if len(parts) == 4 else None
            started = False
            continue
        if "members:" in line:
            started = True
            continue
        if started and current and len(line.split()) == 3:
            counts[current] += 1
    return dict(counts)


def git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(OLD_REPO), *args], check=True, capture_output=True, text=True
    ).stdout


def matches_mfinder_pattern(name: str) -> bool:
    return any(Path(name).match(p) for p in MFINDER_PATTERNS)


def find_mfinder_files() -> dict:
    """Working-tree files, plus the latest committed snapshot of each matching path."""
    working_tree = sorted(
        str(p.relative_to(OLD_REPO))
        for pattern in MFINDER_PATTERNS
        for p in OLD_REPO.rglob(pattern)
        if ".git" not in p.parts
    )
    history: dict[str, dict] = {}
    log = git("log", "--all", "--format=@%H %ad", "--date=short", "--name-status")
    commit = date = None
    for line in log.splitlines():
        if line.startswith("@"):
            commit, date = line[1:].split()
        elif line and line[0] in "AM" and matches_mfinder_pattern(line.split("\t")[-1]):
            path = line.split("\t")[-1]
            history.setdefault(path, {"commit": commit, "date": date})  # log is newest first
    return {"working_tree": working_tree, "history": dict(sorted(history.items()))}


def read_mfinder_file(path: str, found: dict) -> tuple[str, str]:
    if path in found["working_tree"]:
        return (OLD_REPO / path).read_text(encoding="utf-8"), "working tree"
    snap = found["history"][path]
    return git("show", f"{snap['commit']}:{path}"), f"git {snap['commit'][:7]} ({snap['date']})"


def audit_mfinder() -> dict:
    found = find_mfinder_files()
    tag = git("tag", "-l").split()
    result = {"old_repo": str(OLD_REPO), "tags": tag, "found": found, "inputs": {}, "out": {}, "members": {}}
    for path in sorted(set(found["working_tree"]) | set(found["history"])):
        content, source = read_mfinder_file(path, found)
        name = Path(path).name
        if name.endswith("_mfinder_input.txt"):
            result["inputs"][name] = {"source": source, **audit_mfinder_input(content)}
        elif name.endswith("_OUT.txt"):
            z = parse_out_zscores(content)
            result["out"][name] = {"source": source, "motifs": len(z), "flagged": flag_zscores(z)}
        elif name.endswith("_MEMBERS.txt"):
            m = parse_members(content)
            result["members"][name] = {
                "source": source,
                "members_per_motif": m,
                "motifs_at_cap": [k for k, v in m.items() if v == MEMBERS_CAP],
            }
    return result


# --- Markdown report -------------------------------------------------------


def md_table(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(out)


def md_escape(s: str) -> str:
    return s.replace("|", "\\|").replace("`", "\\`")


def render_markdown(texts: list[dict], mfinder: dict) -> str:
    L = ["# Data audit", ""]
    L += [
        "Generated by `scripts/audit_data.py`. Raw files were read only; nothing in",
        "`data/raw/` or the old repo was modified.",
        "",
        "## Input handling decisions",
        "",
        "Decided by the project owner; to be carried into METHODS.md.",
        "",
        '1. Raw files are read with encoding `"utf-8-sig"`, which strips the BOM.',
        "2. CRLF (and any lone CR) line endings are normalized to `\\n` in memory. Raw files are never rewritten.",
        "3. A paragraph is a non-empty line (not a blank-line-separated block). Blank lines and the",
        "   line-length distribution are reported to confirm one-paragraph-per-line holds.",
        "",
        "Audit-specific definitions (not yet decisions for the pipeline):",
        "",
        "- *Word token*: maximal run of Cyrillic letters (incl. ё, ѣ, ѳ, і, ѵ), with internal ASCII",
        "  hyphens joining parts into one token. Digits, Latin letters and punctuation are excluded.",
        "  Characters after BOM strip and newline normalization.",
        "- *Sentences*: `razdel.sentenize` on the full original-case text.",
        "- *Blank line*: a line that is empty or whitespace-only. Line lengths are over non-empty lines.",
        "- *Final ъ*: counted per hyphen-separated part, when ъ follows a consonant (й counted as consonant).",
        "- *Endings -аго/-яго/-ыя/-ія*: per hyphen-separated part, lowercased. Modern words can match",
        "  (e.g. names); see examples.",
        "- *Unusual characters*: anything that is not U+0400–U+04FF, not `.,;:!?()` or the",
        "  inventory punctuation below, and not a plain space or newline (so tabs and NBSP show up).",
        "- Examples show up to 5 lines, trimmed to ±60 characters around the first match.",
        "",
    ]

    labels = [t["label"] for t in texts]

    L += ["## File basics", ""]
    L.append(md_table(
        ["File", "Label", "Bytes", "UTF-8", "BOM", "Line endings", "Lines", "SHA-256"],
        [[t["file"], t["label"], t["basics"]["size_bytes"], t["basics"]["valid_utf8"], t["basics"]["bom"],
          t["basics"]["line_ending_style"], t["basics"]["line_count"], f"`{t['basics']['sha256']}`"]
         for t in texts],
    ))
    L += ["", "## Lengths", ""]
    L.append(md_table(
        ["Label", "Chars", "Paragraphs", "Blank lines", "Line len min / median / max",
         "Sentences", "Word tokens", "Word types (lc)"],
        [[t["label"], t["lengths"]["characters"], t["lengths"]["paragraphs_non_empty_lines"],
          t["lengths"]["blank_lines"],
          f"{t['lengths']['line_length_min']} / {t['lengths']['line_length_median']} / {t['lengths']['line_length_max']}",
          t["lengths"]["sentences"], t["lengths"]["word_tokens"], t["lengths"]["word_types_lower"]]
         for t in texts],
    ))

    L += ["", "## Pre-reform spelling", ""]
    feature_names = list(texts[0]["prereform"])
    L.append(md_table(["Feature"] + labels,
                      [[f] + [t["prereform"][f]["count"] for t in texts] for f in feature_names]))
    L += ["", "### Examples", ""]
    for t in texts:
        shown = {f: v for f, v in t["prereform"].items() if v["examples"]}
        if not shown:
            continue
        L += [f"#### {t['label']}", ""]
        for f, v in shown.items():
            L.append(f"- **{f}** ({v['count']})")
            L += [f"  - L{e['line']}: {md_escape(e['text'])}" for e in v["examples"]]
        L.append("")

    L += ["## ё usage", ""]
    L.append(md_table(["Label", "ё/Ё", "ё per 1,000 words", "«всё»", "«все»"],
                      [[t["label"], t["yo"]["yo_count"], t["yo"]["yo_per_1000_words"],
                        t["yo"]["vsyo"], t["yo"]["vse"]] for t in texts]))

    L += ["", "## Punctuation inventory", ""]
    names = {"—": "em dash —", "–": "en dash –", "-": "hyphen-minus -", "«": "«", "»": "»",
             '"': 'straight "', "„": "„", "“": "“", "”": "”", "'": "straight '", "…": "…"}
    rows = [[names[p]] + [t["punctuation"]["counts"][p] for t in texts] for p in PUNCTUATION]
    rows += [[f"lines starting with {names[d]}"] + [t["punctuation"]["lines_starting_with_dash"][d] for t in texts]
             for d in DIALOGUE_DASHES]
    rows.append(["lines starting with any dash"] + [t["punctuation"]["lines_starting_with_dash_total"] for t in texts])
    L.append(md_table(["Mark"] + labels, rows))

    L += ["", "## Unusual characters", ""]
    for t in texts:
        u = t["unusual"]
        L += [f"### {t['label']}", "", f"{u['distinct']} distinct, {u['total']} total.", ""]
        if u["top"]:
            L.append(md_table(["Char", "Code point", "Name", "Count"],
                              [[f"`{md_escape(r['char'])}`" if r["char"].strip() else "(space)",
                                r["codepoint"], r["name"], r["count"]] for r in u["top"]]))
            L.append("")

    L += ["## Front and back matter", ""]
    for t in texts:
        L += [f"### {t['label']}", "", "First non-empty lines:", "", "```"]
        L += [f"{e['line']:>6}: {e['text']}" for e in t["front_back"]["first"]]
        L += ["```", "", "Last non-empty lines:", "", "```"]
        L += [f"{e['line']:>6}: {e['text']}" for e in t["front_back"]["last"]]
        L += ["```", ""]

    L += render_mfinder_markdown(mfinder)
    return "\n".join(L) + "\n"


def render_mfinder_markdown(m: dict) -> list[str]:
    f = m["found"]
    L = ["## Old mfinder outputs (read-only)", "", f"Repo: `{m['old_repo']}`. Tags present: "
         + (", ".join(m["tags"]) if m["tags"] else "**none**") + ".", ""]
    L += ["### Files found", "", "Working tree:", ""]
    L += [f"- `{p}`" for p in f["working_tree"]] or ["- none"]
    L += ["", "Git history (latest commit that added or modified each path):", ""]
    L += [f"- `{p}` — {v['commit'][:7]} ({v['date']})" for p, v in f["history"].items()] or ["- none"]
    for kind in ("OUT", "MEMBERS"):
        if not any(Path(p).name.endswith(f"_{kind}.txt") for p in [*f["working_tree"], *f["history"]]):
            L += ["", f"**No `*_{kind}.txt` files exist** in the working tree or in git history."]

    L += ["", "### Input files", ""]
    if m["inputs"]:
        L.append(md_table(
            ["File", "Source", "Edges", "Nodes", "Malformed rows", "Duplicate pairs", "Self-loops",
             "Weights all 1", "Weights ≠ 1", "Max weight"],
            [[k, v["source"], v["edges"], v["nodes"], v["malformed_rows"], v["duplicate_pairs"],
              v["self_loops"], v["weights_all_one"], v["weights_not_one"], v["weight_max"]]
             for k, v in m["inputs"].items()],
        ))
    else:
        L.append("None found.")

    L += ["", "### OUT files: Z-scores = 888888 or |Z| > 1000", ""]
    if m["out"]:
        for k, v in m["out"].items():
            flagged = ", ".join(f"motif {x['motif']}: {x['z']}" for x in v["flagged"]) or "none"
            L.append(f"- `{k}` ({v['source']}, {v['motifs']} motifs): {flagged}")
    else:
        L.append("Not checked: no OUT files found.")

    L += ["", f"### MEMBERS files: members per motif (cap {MEMBERS_CAP})", ""]
    if m["members"]:
        for k, v in m["members"].items():
            per = ", ".join(f"{mid}: {n}" for mid, n in v["members_per_motif"].items())
            L.append(f"- `{k}` ({v['source']}): {per}; at cap: {', '.join(v['motifs_at_cap']) or 'none'}")
    else:
        L.append("Not checked: no MEMBERS files found.")
    return L + [""]


# --- Main ------------------------------------------------------------------


def main() -> None:
    texts = [audit_text(RAW_DIR / name, label) for name, label in TEXTS]
    mfinder = audit_mfinder()
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    (AUDIT_DIR / "data_audit.json").write_text(
        json.dumps({"texts": texts, "mfinder": mfinder}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (AUDIT_DIR / "data_audit.md").write_text(render_markdown(texts, mfinder), encoding="utf-8")
    print(f"Wrote {AUDIT_DIR / 'data_audit.md'} and data_audit.json")


if __name__ == "__main__":
    main()
