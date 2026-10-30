"""Checks used by scripts/reproduce.sh.

raw:    SHA-256 of every data/raw/TEXT.txt against the table in data/PROVENANCE.md ([paths].provenance).
nreal:  N_real of each motif in mfinder runs (results.tsv written by motifs.py) against the "All statistics"
        table of the committed docs/motifs/motif_report.md. N_real is the count in the real network, so it does
        not depend on the randomization or on R; a quick (R = 2) run must give the committed values.

Usage:
    uv run --frozen python -m huckfinn_cna.reproduce_check raw
    uv run --frozen python -m huckfinn_cna.reproduce_check nreal RUN_DIR [RUN_DIR ...]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
import sys
from pathlib import Path

from huckfinn_cna.config import DEFAULT_CONFIG, load_config, repo_path

SHA_CELL = re.compile(r"`([0-9a-f]{64})`")


def provenance_checksums(path: Path) -> dict[str, str]:
    """File name -> SHA-256 from the provenance table (rows `| Ru1.txt | label | `sha` | ...`)."""
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        m = SHA_CELL.search(line)
        if m and cells and cells[0].endswith(".txt"):
            out[cells[0]] = m.group(1)
    return out


def check_raw(config: dict) -> list[str]:
    """Problems found (empty = every raw text present with the expected checksum)."""
    expected = provenance_checksums(repo_path(config, "provenance"))
    raw = repo_path(config, "raw_dir")
    problems = []
    for text in config["corpus"]["texts"]:
        name = f"{text}.txt"
        if name not in expected:
            problems.append(f"{name}: no checksum in {config['paths']['provenance']}")
        elif not (raw / name).exists():
            problems.append(f"{name}: missing from {config['paths']['raw_dir']}")
        elif (digest := hashlib.sha256((raw / name).read_bytes()).hexdigest()) != expected[name]:
            problems.append(f"{name}: SHA-256 {digest}, expected {expected[name]}")
    return problems


def report_n_real(path: Path) -> dict[tuple[str, int], int]:
    """(text, mfinder ID) -> N_real from the motif report's "All statistics" table."""
    out, in_table = {}, False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            in_table = line.strip() == "## All statistics"
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if in_table and len(cells) > 2 and cells[1].isdigit():
            out[(cells[0], int(cells[1]))] = int(cells[2].replace(",", ""))
    return out


def run_n_real(run_dir: Path) -> tuple[str, dict[int, int]]:
    """Text id (from run.log) and mfinder ID -> N_real from the run's results.tsv."""
    log = (run_dir / "run.log").read_text(encoding="utf-8")
    text = re.search(r"^text: (\S+)$", log, re.MULTILINE).group(1)
    with (run_dir / "results.tsv").open(encoding="utf-8") as fh:
        return text, {int(r["mfinder_id"]): int(r["n_real"]) for r in csv.DictReader(fh, delimiter="\t")}


def check_n_real(config: dict, run_dirs: list[Path]) -> list[str]:
    committed = report_n_real(repo_path(config, "motif_report"))
    problems = []
    for d in run_dirs:
        text, counts = run_n_real(d)
        for motif, n in counts.items():
            want = committed.get((text, motif))
            if want != n:
                problems.append(f"{text} motif {motif}: N_real {n}, committed {want}")
        if len(counts) != len(config["networks"]["triads"]):
            problems.append(f"{text}: {len(counts)} motifs in results.tsv")
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    sub = parser.add_subparsers(dest="check", required=True)
    sub.add_parser("raw")
    nreal = sub.add_parser("nreal")
    nreal.add_argument("run_dirs", nargs="+", type=Path)
    args = parser.parse_args()
    config = load_config(args.config)
    if args.check == "raw":
        problems = check_raw(config)
        ok = f"raw texts: all {len(config['corpus']['texts'])} match {config['paths']['provenance']}"
    else:
        problems = check_n_real(config, args.run_dirs)
        ok = f"N_real: all {len(args.run_dirs)} runs match the committed {config['paths']['motif_report']}"
    for p in problems:
        print(p, file=sys.stderr)
    if problems:
        sys.exit(1)
    print(ok)


if __name__ == "__main__":
    main()
