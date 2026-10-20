"""Data audit for the five Russian translations and the old mfinder outputs.

Reads data/raw/Ru1.txt .. Ru5.txt (never writes to them) and the old repo
(read-only, including read-only `git show` of committed snapshots), and writes
docs/audit/data_audit.md, docs/audit/data_audit.json and one
docs/audit/old_mfinder_zscores_<run>.csv per mfinder run found.

Usage:
    .venv/bin/python scripts/audit_data.py
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import statistics
import subprocess
import unicodedata
from collections import Counter
from datetime import datetime
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
# OUT file suffix -> run name. Members files also end in _OUT.txt, so they are matched first.
MFINDER_RUNS = {"_mfinder_input_OUT.txt": "input_OUT", "_mfinder_stats_OUT.txt": "stats_OUT"}
MEMBERS_SUFFIXES = ("_members_OUT.txt", "_MEMBERS.txt")
ZSCORE_SENTINEL = 888888.0
ZSCORE_SUSPECT_ABOVE = 1000.0
MEMBERS_CAP = 1000
WEIGHT_BINS = ((1, 1), (2, 2), (3, 5), (6, 10), (11, None))  # inclusive; None = no upper bound
ZSCORE_CSV_STEM = "old_mfinder_zscores"
# Raw Z-scores printed in Figure 9 of the old repo's "Final Report 3.pdf", transcribed by the project owner.
FIGURE9_CSV = AUDIT_DIR / "report3_fig9_zscores.csv"
ZSCORE_MATCH_DECIMALS = 2

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


def classify_mfinder_file(name: str) -> str | None:
    if name.endswith(MEMBERS_SUFFIXES):
        return "members"
    if name.endswith(tuple(MFINDER_RUNS)):
        return "out"
    if name.endswith("_mfinder_input.txt"):
        return "input"
    return None


def mfinder_run(name: str) -> str:
    return next(run for suffix, run in MFINDER_RUNS.items() if name.endswith(suffix))


def text_id(name: str) -> str:
    return name.split("_")[0]


def parse_edges(content: str) -> list[tuple[str, ...]]:
    return [tuple(ln.split()) for ln in content.splitlines() if ln.strip()]


def weight_bin_label(lo: int, hi: int | None) -> str:
    if hi is None:
        return f"{lo}+"
    return f"{lo}" if lo == hi else f"{lo}-{hi}"


def weight_distribution(weights: list[float]) -> dict:
    return {
        "bins": {
            weight_bin_label(lo, hi): sum(1 for w in weights if w >= lo and (hi is None or w <= hi))
            for lo, hi in WEIGHT_BINS
        },
        "sum": sum(weights),
        "mean": round(statistics.fmean(weights), 3) if weights else None,
        "median": statistics.median(weights) if weights else None,
        "max": max(weights, default=None),
    }


def audit_mfinder_input(content: str) -> dict:
    edges = parse_edges(content)
    pairs = Counter((e[0], e[1]) for e in edges if len(e) >= 2)
    duplicates = {f"{s} {t}": n for (s, t), n in pairs.items() if n > 1}
    weights = [float(e[2]) for e in edges if len(e) >= 3]
    return {
        "edges": len(edges),
        "nodes": len({n for pair in pairs for n in pair}),
        "malformed_rows": sum(1 for e in edges if len(e) != 3),
        "duplicate_pairs": len(duplicates),
        "duplicate_examples": dict(list(duplicates.items())[:MAX_EXAMPLES]),
        "self_loops": sum(1 for s, t in pairs if s == t),
        "weights_all_one": bool(weights) and all(w == 1 for w in weights),
        "weights": weight_distribution(weights),
    }


OUT_META_PATTERNS = {
    "nodes": r"Num of Nodes:\s*(\d+)",
    "edges": r"Num of Edges:\s*(\d+)",
    "random_networks": r"Number of random networks generated\s*:\s*(\d+)",
    "criteria_zscore": r"Criteria taken\s*:\s*Nreal Zscore\s*>\s*([-\d.]+)",
    "motifs_passing_criteria": r"Full list includes (\d+) motifs",
    "total_runtime": r"Application total runtime was:\s*([\d.]+\s*\w+)",
    "single_random_runtime": r"Single Random network processing runtime was:\s*([\d.]+\s*\w+)",
}


def parse_out(content: str) -> dict:
    """Header metadata plus the 'Full list of subgraphs' table (ID NREAL NRAND+-STD Z PVAL CREAL UNIQ)."""
    meta = {}
    for key, pattern in OUT_META_PATTERNS.items():
        match = re.search(pattern, content)
        meta[key] = match.group(1) if match else None
    motifs = {}
    in_block = False
    for line in content.splitlines():
        line = line.strip()
        if "Full list of subgraphs size" in line:
            in_block = True
            continue
        parts = line.split()
        if not in_block or len(parts) < 5 or not parts[0].isdigit() or "+-" not in parts[2]:
            continue
        try:
            mean, std = (float(x) for x in parts[2].split("+-"))
            motifs[int(parts[0])] = {
                "nreal": int(parts[1]), "nrand_mean": mean, "nrand_std": std,
                "z": float(parts[3]), "pval": float(parts[4]),
            }
        except ValueError:
            continue
    return {"meta": meta, "motifs": motifs}


def flag_zscores(zscores: dict[int, float]) -> list[dict]:
    return [
        {"motif": m, "z": z, "sentinel": z == ZSCORE_SENTINEL}
        for m, z in sorted(zscores.items())
        if z == ZSCORE_SENTINEL or abs(z) > ZSCORE_SUSPECT_ABOVE
    ]


def parse_members(content: str) -> dict[str, dict]:
    """Motif ID -> count in the 'list of N members:' header, member rows, and distinct node triples."""
    found: dict[str, dict] = {}
    current = None
    started = False
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("subgraph id ="):
            parts = line.split()
            current = parts[3] if len(parts) == 4 else None
            if current:
                found[current] = {"header_n": None, "rows": 0, "node_sets": set()}
            started = False
            continue
        if "members:" in line and current:
            match = re.search(r"list of (\d+) members", line)
            found[current]["header_n"] = int(match.group(1)) if match else None
            started = True
            continue
        parts = line.split()
        if started and current and len(parts) == 3:
            found[current]["rows"] += 1
            found[current]["node_sets"].add(frozenset(parts))
    return {
        m: {"header_n": v["header_n"], "rows": v["rows"], "unique_node_sets": len(v["node_sets"])}
        for m, v in found.items()
    }


def at_members_cap(entry: dict) -> bool:
    return MEMBERS_CAP in (entry["header_n"], entry["rows"], entry["unique_node_sets"])


def git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(OLD_REPO), *args], check=True, capture_output=True, text=True
    ).stdout


def matches_mfinder_pattern(name: str) -> bool:
    return any(Path(name).match(p) for p in MFINDER_PATTERNS)


def find_mfinder_files() -> dict:
    """Working-tree files with modification dates, plus the latest committed snapshot of each path."""
    paths = sorted(
        {p for pattern in MFINDER_PATTERNS for p in OLD_REPO.rglob(pattern) if ".git" not in p.parts}
    )
    working_tree = [
        {
            "path": str(p.relative_to(OLD_REPO)),
            "kind": classify_mfinder_file(p.name),
            "modified": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
            "bytes": p.stat().st_size,
        }
        for p in paths
    ]
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


def git_snapshot(path: str, snap: dict) -> bytes:
    return subprocess.run(
        ["git", "-C", str(OLD_REPO), "show", f"{snap['commit']}:{path}"], check=True, capture_output=True
    ).stdout


def audit_mfinder() -> dict:
    found = find_mfinder_files()
    result = {
        "old_repo": str(OLD_REPO), "tags": git("tag", "-l").split(), "found": found,
        "inputs": {}, "out": {}, "members": {},
    }
    history_by_name = {Path(p).name: (p, snap) for p, snap in found["history"].items()}

    for f in found["working_tree"]:
        name = Path(f["path"]).name
        raw = (OLD_REPO / f["path"]).read_bytes()
        content = raw.decode("utf-8")
        if f["kind"] == "input":
            entry = {"source": f["path"], "sha256": hashlib.sha256(raw).hexdigest(), **audit_mfinder_input(content)}
            if name in history_by_name:
                hpath, snap = history_by_name[name]
                entry["git_snapshot"] = f"{snap['commit'][:7]} ({snap['date']})"
                entry["identical_to_git_snapshot"] = git_snapshot(hpath, snap) == raw
            result["inputs"][name] = entry
        elif f["kind"] == "out":
            parsed = parse_out(content)
            zscores = {m: row["z"] for m, row in parsed["motifs"].items()}
            result["out"][name] = {
                "source": f["path"], "text": text_id(name), "run": mfinder_run(name),
                **parsed, "flagged": flag_zscores(zscores),
            }
        elif f["kind"] == "members":
            members = parse_members(content)
            result["members"][name] = {
                "source": f["path"], "text": text_id(name), "members_per_motif": members,
                "motifs_at_cap": [m for m, v in members.items() if at_members_cap(v)],
            }

    # Inputs that exist only in git history are still audited from the snapshot.
    for name, (hpath, snap) in history_by_name.items():
        if classify_mfinder_file(name) == "input" and name not in result["inputs"]:
            result["inputs"][name] = {
                "source": f"git {snap['commit'][:7]} ({snap['date']})",
                **audit_mfinder_input(git_snapshot(hpath, snap).decode("utf-8")),
            }

    if FIGURE9_CSV.exists():
        result["figure9"] = {
            "source": str(FIGURE9_CSV.relative_to(REPO_ROOT)),
            **compare_figure_to_runs(read_figure_zscores(FIGURE9_CSV), run_zscores(result)),
        }
    return result


def zscore_table(mfinder: dict, run: str) -> tuple[list[int], list[dict]]:
    """Rows of (text, label, file, Z per motif) for one run, ordered by text."""
    labels = {Path(name).stem: label for name, label in TEXTS}
    outs = sorted((o for o in mfinder["out"].values() if o["run"] == run), key=lambda o: o["text"])
    motifs = sorted({m for o in outs for m in o["motifs"]})
    rows = [
        {"text": o["text"], "label": labels.get(o["text"], ""), "file": Path(o["source"]).name,
         "z": {m: o["motifs"].get(m, {}).get("z") for m in motifs}}
        for o in outs
    ]
    return motifs, rows


def mfinder_runs(mfinder: dict) -> list[str]:
    return sorted({o["run"] for o in mfinder["out"].values()})


def write_zscore_csvs(mfinder: dict) -> list[Path]:
    paths = []
    for run in mfinder_runs(mfinder):
        motifs, rows = zscore_table(mfinder, run)
        path = AUDIT_DIR / f"{ZSCORE_CSV_STEM}_{run}.csv"
        with path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["text", "label", "file"] + [f"motif_{m}" for m in motifs])
            for r in rows:
                writer.writerow([r["text"], r["label"], r["file"]] + [r["z"][m] for m in motifs])
        paths.append(path)
    return paths


def read_figure_zscores(path: Path) -> dict[str, dict[int, float]]:
    """Text -> motif -> Z from a CSV with a 'text' column and one column per motif ID."""
    with path.open(encoding="utf-8", newline="") as fh:
        return {row.pop("text"): {int(m): float(z) for m, z in row.items()} for row in csv.DictReader(fh)}


def compare_figure_to_runs(figure: dict[str, dict[int, float]], runs: dict[str, dict[str, dict[int, float]]]) -> dict:
    """Cell-by-cell match (rounded to ZSCORE_MATCH_DECIMALS) of figure values against each run's Z-scores."""
    same = lambda a, b: b is not None and round(a, ZSCORE_MATCH_DECIMALS) == round(b, ZSCORE_MATCH_DECIMALS)
    cells = [(t, m, z) for t, row in figure.items() for m, z in row.items()]
    per_run = {
        run: {
            "matched": sum(1 for t, m, z in cells if same(z, zs.get(t, {}).get(m))),
            "mismatches": [
                {"text": t, "motif": m, "figure": z, "run": zs.get(t, {}).get(m)}
                for t, m, z in cells if not same(z, zs.get(t, {}).get(m))
            ],
        }
        for run, zs in runs.items()
    }
    neither = [
        {"text": t, "motif": m, "figure": z, **{run: zs.get(t, {}).get(m) for run, zs in runs.items()}}
        for t, m, z in cells
        if not any(same(z, zs.get(t, {}).get(m)) for zs in runs.values())
    ]
    return {"cells": len(cells), "runs": per_run, "matches_neither": neither}


def run_zscores(mfinder: dict) -> dict[str, dict[str, dict[int, float]]]:
    """Run -> text -> motif -> Z, from the parsed OUT files."""
    result: dict[str, dict[str, dict[int, float]]] = {}
    for o in mfinder["out"].values():
        result.setdefault(o["run"], {})[o["text"]] = {m: row["z"] for m, row in o["motifs"].items()}
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
    L = ["## Old mfinder outputs (read-only)", "",
         f"Repo: `{m['old_repo']}`. Tags present: " + (", ".join(m["tags"]) if m["tags"] else "**none**") + ".",
         "Files were only read; git snapshots were read with `git show`.", ""]

    L += ["### Files found", "", "Working tree (modified = filesystem mtime):", ""]
    if f["working_tree"]:
        L.append(md_table(["Path", "Kind", "Modified", "Bytes"],
                          [[f"`{w['path']}`", w["kind"], w["modified"], w["bytes"]] for w in f["working_tree"]]))
    else:
        L.append("- none")
    L += ["", "Git history (latest commit that added or modified each path):", ""]
    L += [f"- `{p}` — {v['commit'][:7]} ({v['date']})" for p, v in f["history"].items()] or ["- none"]
    L += ["", "Naming patterns:", "",
          "- `*_mfinder_input.txt`: edge lists (source, target, weight) given to mfinder.",
          "- `*_mfinder_input_OUT.txt`: mfinder summary from one run (listing criteria Z > 2, Mfactor > 1.1,",
          "  uniqueness ≥ 4). The name is mfinder's default `<input>_OUT.txt`.",
          "- `*_mfinder_stats_OUT.txt`: mfinder summary from a second run (criteria Z > 0, Mfactor > 0,",
          "  uniqueness ≥ 0, so every over-represented motif is listed with its adjacency matrix).",
          "- `*_mfinder_members_OUT.txt`: member lists (`-omem`) written alongside the second run;",
          "  dates match `stats_OUT`. No `*_MEMBERS.txt` files exist.",
          "",
          "The two OUT files per text are **separate runs** on the same network: NREAL is identical but the",
          "random-network means, SDs and therefore Z-scores differ, and runtimes and dates differ."]

    L += ["", "### Input files", ""]
    if m["inputs"]:
        bins = [weight_bin_label(lo, hi) for lo, hi in WEIGHT_BINS]
        L.append(md_table(
            ["File", "Edges", "Nodes", "Malformed", "Dup. pairs", "Self-loops", "Weights all 1",
             "Weight mean / median / max"] + [f"w = {b}" for b in bins] + ["Identical to git snapshot"],
            [[k, v["edges"], v["nodes"], v["malformed_rows"], v["duplicate_pairs"], v["self_loops"],
              v["weights_all_one"], f"{v['weights']['mean']} / {v['weights']['median']} / {v['weights']['max']}"]
             + [v["weights"]["bins"][b] for b in bins]
             + [f"{v['identical_to_git_snapshot']} ({v['git_snapshot']})" if "git_snapshot" in v else "n/a"]
             for k, v in m["inputs"].items()],
        ))
    else:
        L.append("None found.")

    if not m["out"]:
        L += ["", "### OUT files", "", "Not checked: no OUT files found."]
    else:
        L += ["", "### OUT files: run settings", ""]
        L.append(md_table(
            ["File", "Run", "Nodes", "Edges", "Random networks", "Listing criterion Z >", "Motifs listed",
             "Total runtime", "Per random network"],
            [[k, v["run"], v["meta"]["nodes"], v["meta"]["edges"], v["meta"]["random_networks"],
              v["meta"]["criteria_zscore"], v["meta"]["motifs_passing_criteria"], v["meta"]["total_runtime"],
              v["meta"]["single_random_runtime"]] for k, v in m["out"].items()],
        ))
        L += ["", "### OUT files: Z = 888888 or |Z| > 1000", ""]
        for k, v in m["out"].items():
            flagged = ", ".join(f"motif {x['motif']}: {x['z']}" for x in v["flagged"]) or "none"
            L.append(f"- `{k}` ({len(v['motifs'])} motifs): {flagged}")

        tables = {run: zscore_table(m, run) for run in mfinder_runs(m)}
        for run, (motifs, rows) in tables.items():
            L += ["", f"### Z-scores, run `{run}` (`docs/audit/{ZSCORE_CSV_STEM}_{run}.csv`)", ""]
            L.append(md_table(["Motif"] + [r["label"] for r in rows],
                              [[mid] + [r["z"][mid] for r in rows] for mid in motifs]))
        if len(tables) == 2:
            (run_a, (motifs, rows_a)), (run_b, (_, rows_b)) = tables.items()
            L += ["", f"### Z-score difference between runs (`{run_b}` − `{run_a}`)", ""]
            deltas = [[zscore_delta(b["z"].get(mid), a["z"].get(mid)) for a, b in zip(rows_a, rows_b)]
                      for mid in motifs]
            L.append(md_table(
                ["Motif"] + [r["label"] for r in rows_a],
                [[mid] + [fmt_delta(d) for d in row] for mid, row in zip(motifs, deltas)]
                + [["max \\|Δ\\|"] + [max((abs(row[i]) for row in deltas if row[i] is not None), default="")
                                       for i in range(len(rows_a))]],
            ))

    L += ["", "### Comparison with Figure 9 of `Final Report 3.pdf`", ""]
    fig = m.get("figure9")
    if fig:
        L += [f"Figure values: `{fig['source']}` (raw Z-scores read off the figure by the project owner).",
              f"Cells compared after rounding both sides to {ZSCORE_MATCH_DECIMALS} decimals.", ""]
        L.append(md_table(["Run", "Cells matching", "Cells not matching"],
                          [[run, f"{r['matched']} / {fig['cells']}", len(r["mismatches"])]
                           for run, r in fig["runs"].items()]))
        L += ["", f"Cells matching neither run: {len(fig['matches_neither'])}."]
        if fig["matches_neither"]:
            runs = list(fig["runs"])
            L += ["", md_table(["Text", "Motif", "Figure"] + runs,
                               [[c["text"], c["motif"], c["figure"]] + [c[r] for r in runs]
                                for c in fig["matches_neither"]])]
    else:
        L.append(f"Not done: `{FIGURE9_CSV.relative_to(REPO_ROOT)}` not found.")

    if m["inputs"]:
        labels = {Path(name).stem: label for name, label in TEXTS}
        ranked = sorted(m["inputs"].items(), key=lambda kv: -kv[1]["edges"])
        L += ["", "### Network size per text (old input files)", "",
              "Ranked by edge count. Edges are distinct directed word pairs (weights not summed);",
              "weighted total = sum of the weight column.", ""]
        L.append(md_table(
            ["Rank", "Text", "Label", "Nodes", "Edges", "Edges per node", "Weighted total"],
            [[i, text_id(k), labels.get(text_id(k), ""), v["nodes"], v["edges"],
              round(v["edges"] / v["nodes"], 2), int(v["weights"]["sum"])]
             for i, (k, v) in enumerate(ranked, start=1)],
        ))
        rank = {text_id(k): i for i, (k, _) in enumerate(ranked, start=1)}
        top = text_id(ranked[0][0])
        L += ["", f"Final Report 3 says Braude's (Ru4) Z-scores are larger because its network \"contains more "
              f"connections overall\". By edge count Ru4 ranks {rank.get('Ru4')} of {len(ranked)}; the largest "
              f"network is {top} ({labels.get(top, '')})."]

    L += ["", f"### MEMBERS files: members per motif (cap {MEMBERS_CAP})", "",
          "Each cell: count in mfinder's `Partial list of N members:` header / member rows listed /",
          "distinct unordered node triples among those rows.", ""]
    if m["members"]:
        files = list(m["members"])
        motif_ids = sorted({mid for v in m["members"].values() for mid in v["members_per_motif"]}, key=int)
        L.append(md_table(
            ["Motif"] + [m["members"][k]["text"] for k in files],
            [[mid] + [members_cell(m["members"][k]["members_per_motif"].get(mid)) for k in files]
             for mid in motif_ids],
        ))
        L.append("")
        L += [f"- `{k}`: motifs at exactly {MEMBERS_CAP}: {', '.join(m['members'][k]['motifs_at_cap']) or 'none'}"
              for k in files]
        same = [mid for mid in motif_ids
                if len({members_cell(m["members"][k]["members_per_motif"].get(mid)) for k in files}) == 1]
        L += ["", f"Motifs whose member counts are identical in all {len(files)} texts: "
              f"{len(same)} of {len(motif_ids)}. Since NREAL is in the thousands to millions and the header says",
              "`Partial list`, these lists are truncated samples, not complete member sets. The counts fit",
              "1001 × 6/|Aut(motif)| (e.g. 238, fully mutual triad, 6 automorphisms → 1001; 38, feed-forward",
              "loop, 1 automorphism → 6006); that mechanism is inferred, not verified in mfinder's source."]
    else:
        L.append("Not checked: no MEMBERS files found.")
    return L + [""]


def zscore_delta(b: float | None, a: float | None) -> float | None:
    return None if a is None or b is None else round(b - a, 2)


def fmt_delta(d: float | None) -> str:
    return "" if d is None else f"{d:+.2f}"


def members_cell(entry: dict | None) -> str:
    return "" if entry is None else f"{entry['header_n']} / {entry['rows']} / {entry['unique_node_sets']}"


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
    csvs = write_zscore_csvs(mfinder)
    print(f"Wrote {AUDIT_DIR / 'data_audit.md'}, data_audit.json and {', '.join(p.name for p in csvs)}")


if __name__ == "__main__":
    main()
