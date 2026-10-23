"""Motif statistics from an mfinder run: data/interim/mfinder/<run>/ -> <run>/results.tsv + report.

Reads a run folder written by scripts/run_mfinder.sh: run.log (text, R, seed, input checksum),
TEXT_..._OUT.txt (mfinder's summary) and TEXT_..._MAT_R.txt (-orall: the count of every subgraph in
every random network; one row per subgraph ID, ascending; one column per random network).

For each of the 13 connected triads (config [[networks.triads]]):
- N_real (from _OUT.txt), checked against the exact igraph/networkx count of the run's input network;
- null mean and SD over the R random networks (SD with ddof [motifs].null_sd_ddof, as mfinder);
  Z = (N_real - mean) / SD, checked against mfinder's printed Z;
- one-sided empirical p-values (b + 1) / (R + 1), b = random networks with a count >= N_real (over)
  or <= N_real (under); Benjamini-Hochberg q-values over the 13 triads, for each direction;
- abundance ratio (N_real - mean) / (N_real + mean + epsilon) (Milo et al. 2004);
- concentration N_real / sum of N_real over the 13 triads;
- significance profile SP = Z / ||Z|| (Milo et al. 2004);
- approximate standard error of Z from R random networks, sqrt((1 + Z^2 / 2) / R): the delta method
  for Z = (x - m) / s with a normal null, where Var(m) = s^2 / R and Var(s) ~ s^2 / (2R).
Flags: N_real or null mean below [motifs].min_count; the sentinel [motifs].sentinel anywhere in the
mfinder output; SD = 0 (Z undefined).

Usage:
    uv run python -m huckfinn_cna.motifs RUN_DIR [RUN_DIR ...] [--network PATH] [--report PATH]
--network gives the run's input network when it is no longer data/interim/networks/TEXT_mfinder.txt;
its SHA-256 must equal the one in run.log.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import math
import re
from pathlib import Path

from huckfinn_cna.config import DEFAULT_CONFIG, REPO_ROOT, load_config, repo_path
from huckfinn_cna.networks import compare_nreal, config_classes

COLUMNS = ["mfinder_id", "triad_code", "n_real", "null_mean", "null_sd", "z", "z_se", "p_over", "p_under",
           "q_over", "q_under", "abundance_ratio", "concentration", "sp", "mfinder_z", "flags"]


class MotifError(Exception):
    pass


# --- Parsing ----------------------------------------------------------------------


def read_run_log(path: Path) -> dict:
    """Fields of run.log ('key: value' lines) plus text, r, seed and input_sha256."""
    fields = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition(": ")
        if sep:
            fields[key] = value
    try:
        return fields | {"text": fields["text"], "r": int(fields["random networks"]),
                         "seed": int(fields["seed"].split()[0]), "input_sha256": fields["input sha256"]}
    except (KeyError, ValueError) as e:
        raise MotifError(f"{path}: missing or malformed field ({e})") from e


# One row of the 'Full list of subgraphs' table: ID NREAL mean+-sd Z PVAL CREAL UNIQ
FULL_LIST_ROW = re.compile(r"^(\d+)\s+(\d+)\s+(\S+)\+-(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s*$")


def read_out(path: Path) -> dict[int, dict]:
    """mfinder ID -> {n_real, mean, sd, z, pval, creal, uniq} from the 'Full list of subgraphs' section."""
    lines = path.read_text(encoding="latin-1").splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.startswith("Full list of subgraphs")), None)
    if start is None:
        raise MotifError(f"no 'Full list of subgraphs' section in {path}")
    rows = {}
    for ln in lines[start + 1:]:
        m = FULL_LIST_ROW.match(ln)
        if m:
            sid, n_real, mean, sd, z, pval, creal, uniq = m.groups()
            rows[int(sid)] = {"n_real": int(n_real), "mean": float(mean), "sd": float(sd), "z": float(z),
                              "pval": float(pval), "creal": float(creal), "uniq": int(uniq)}
    return rows


def read_mat_r(path: Path) -> list[list[int]]:
    """One list of random-network counts per subgraph, in ascending ID order."""
    return [[int(x) for x in ln.split()] for ln in path.read_text(encoding="latin-1").splitlines() if ln.strip()]


# --- Statistics -------------------------------------------------------------------


def mean_sd(values: list[int], ddof: int) -> tuple[float, float]:
    n = len(values)
    if n <= ddof:
        raise MotifError(f"{n} random networks: SD with ddof={ddof} needs more")
    m = sum(values) / n
    return m, math.sqrt(sum((v - m) ** 2 for v in values) / (n - ddof))


def z_score(n_real: int, mean: float, sd: float) -> float:
    return (n_real - mean) / sd if sd > 0 else math.nan


def empirical_p(n_real: int, values: list[int]) -> tuple[float, float]:
    """One-sided (b + 1) / (R + 1) for over-representation (count >= N_real) and under (count <= N_real)."""
    r = len(values)
    over = sum(1 for v in values if v >= n_real)
    under = sum(1 for v in values if v <= n_real)
    return (over + 1) / (r + 1), (under + 1) / (r + 1)


def bh_qvalues(pvalues: list[float]) -> list[float]:
    """Benjamini-Hochberg adjusted p-values (step-up, monotone, capped at 1), in input order."""
    m = len(pvalues)
    order = sorted(range(m), key=lambda i: pvalues[i])
    q = [0.0] * m
    running = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        running = min(running, pvalues[i] * m / rank)
        q[i] = running
    return q


def abundance_ratio(n_real: int, mean: float, epsilon: float) -> float:
    return (n_real - mean) / (n_real + mean + epsilon)


def significance_profile(z: list[float]) -> list[float]:
    """Z / ||Z|| over the triads with a defined Z (undefined ones stay NaN)."""
    norm = math.sqrt(sum(v * v for v in z if not math.isnan(v)))
    return [v / norm if norm > 0 and not math.isnan(v) else math.nan for v in z]


def z_standard_error(z: float, r: int) -> float:
    return math.sqrt((1 + z * z / 2) / r) if not math.isnan(z) else math.nan


def triad_flags(n_real: int, mean: float, sd: float, out_row: dict, mat_row: list[int], cfg: dict) -> list[str]:
    flags = []
    if n_real < cfg["min_count"]:
        flags.append("n_real_low")
    if mean < cfg["min_count"]:
        flags.append("null_mean_low")
    if sd == 0:
        flags.append("sd_zero")
    sentinel = cfg["sentinel"]
    if sentinel in mat_row or any(out_row[k] == sentinel for k in ("n_real", "mean", "sd", "z", "pval", "creal")):
        flags.append("sentinel")
    return flags


def analyse(out: dict[int, dict], mat: list[list[int]], classes: list[dict], cfg: dict) -> list[dict]:
    """One result row per triad class (config order, ascending mfinder ID)."""
    ids = [c["mfinder_id"] for c in classes]
    if sorted(out) != sorted(ids):
        raise MotifError(f"_OUT.txt subgraph IDs {sorted(out)} differ from the triad table {sorted(ids)}")
    if len(mat) != len(ids):
        raise MotifError(f"_MAT_R.txt has {len(mat)} rows, expected {len(ids)}")
    r = len(mat[0])
    if any(len(row) != r for row in mat):
        raise MotifError("_MAT_R.txt rows have different lengths")
    mat_by_id = dict(zip(sorted(ids), mat))
    rows = []
    for c in classes:
        sid = c["mfinder_id"]
        values, n_real = mat_by_id[sid], out[sid]["n_real"]
        mean, sd = mean_sd(values, cfg["null_sd_ddof"])
        z = z_score(n_real, mean, sd)
        p_over, p_under = empirical_p(n_real, values)
        rows.append({"mfinder_id": sid, "triad_code": c["triad_code"], "n_real": n_real, "null_mean": mean,
                     "null_sd": sd, "z": z, "z_se": z_standard_error(z, r), "p_over": p_over, "p_under": p_under,
                     "abundance_ratio": abundance_ratio(n_real, mean, cfg["abundance_epsilon"]),
                     "mfinder_z": out[sid]["z"],
                     "flags": triad_flags(n_real, mean, sd, out[sid], values, cfg)})
    total = sum(row["n_real"] for row in rows)
    for row, q_over, q_under, sp in zip(rows, bh_qvalues([x["p_over"] for x in rows]),
                                        bh_qvalues([x["p_under"] for x in rows]),
                                        significance_profile([x["z"] for x in rows])):
        row.update(q_over=q_over, q_under=q_under, sp=sp, concentration=row["n_real"] / total if total else math.nan)
    return rows


def check_mfinder_z(rows: list[dict], tolerance: float) -> list[int]:
    """IDs whose recomputed Z differs from mfinder's printed Z by more than the tolerance."""
    return [r["mfinder_id"] for r in rows
            if not (math.isnan(r["z"]) or abs(r["z"] - r["mfinder_z"]) <= tolerance)]


# --- Run folders ------------------------------------------------------------------


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_files(run_dir: Path) -> tuple[Path, Path, Path]:
    log = run_dir / "run.log"
    outs, mats = sorted(run_dir.glob("*_OUT.txt")), sorted(run_dir.glob("*_MAT_R.txt"))
    if not log.exists() or len(outs) != 1 or len(mats) != 1:
        raise MotifError(f"{run_dir}: expected run.log, one _OUT.txt and one _MAT_R.txt")
    return log, outs[0], mats[0]


def analyse_run(run_dir: Path, config: dict, classes: list[dict], network: Path | None = None) -> dict:
    """Statistics for one run folder; writes results.tsv there. Fails unless N_real equals igraph."""
    log_path, out_path, mat_path = run_files(run_dir)
    log = read_run_log(log_path)
    network = network or repo_path(config, "networks_dir") / f"{log['text']}_mfinder.txt"
    if sha256(network) != log["input_sha256"]:
        raise MotifError(f"{network} is not the network this run used (SHA-256 differs from {log_path}); "
                         "pass the run's input with --network")
    nreal_check = compare_nreal(network, out_path, classes)   # raises unless every N_real equals igraph
    mat = read_mat_r(mat_path)
    if mat and len(mat[0]) != log["r"]:
        raise MotifError(f"{mat_path}: {len(mat[0])} random networks, run.log says {log['r']}")
    rows = analyse(read_out(out_path), mat, classes, config["motifs"])
    write_results(run_dir / "results.tsv", rows)
    return {"run_dir": run_dir, "log": log, "network": network, "rows": rows, "nreal_check": nreal_check,
            "z_mismatch": check_mfinder_z(rows, config["motifs"]["mfinder_z_tolerance"])}


def fmt(x, digits: int = 6) -> str:
    if isinstance(x, float):
        return "nan" if math.isnan(x) else f"{x:.{digits}f}"
    return str(x)


def write_results(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
        writer.writerow(COLUMNS)
        for r in rows:
            writer.writerow([",".join(r[c]) if c == "flags" else fmt(r[c]) for c in COLUMNS])


# --- Old vs new -------------------------------------------------------------------


def read_old_z(path: Path) -> dict[str, dict[int, float]]:
    """text -> {mfinder ID: Z} from the Figure 9 table."""
    with path.open(encoding="utf-8", newline="") as fh:
        return {row.pop("text"): {int(k): float(v) for k, v in row.items()} for row in csv.DictReader(fh)}


def old_vs_new(rows: list[dict], old: dict[int, float], z_threshold: float) -> list[dict]:
    """Per triad: old and new Z and SP, differences, and whether the sign or the |Z| > threshold status flips."""
    ids = [r["mfinder_id"] for r in rows]
    old_sp = dict(zip(ids, significance_profile([old[i] for i in ids])))
    out = []
    for r in rows:
        oz, nz = old[r["mfinder_id"]], r["z"]
        out.append({"mfinder_id": r["mfinder_id"], "triad_code": r["triad_code"], "old_z": oz, "new_z": nz,
                    "dz": nz - oz, "old_sp": old_sp[r["mfinder_id"]], "new_sp": r["sp"],
                    "dsp": r["sp"] - old_sp[r["mfinder_id"]],
                    "sign_flip": (oz > 0) != (nz > 0),
                    "sig_flip": (abs(oz) > z_threshold) != (abs(nz) > z_threshold)})
    return out


# --- Report -----------------------------------------------------------------------


def star(z: float, threshold: float) -> str:
    return "*" if abs(z) > threshold else ""


def render_run(res: dict, config: dict, old: dict[str, dict[int, float]]) -> list[str]:
    cfg, log, rows = config["motifs"], res["log"], res["rows"]
    label = config["corpus"]["labels"][log["text"]]
    flagged = [f"{r['mfinder_id']} ({', '.join(r['flags'])})" for r in rows if r["flags"]]
    min_p = 1 / (log["r"] + 1)
    current = repo_path(config, "networks_dir") / f"{log['text']}_mfinder.txt"
    if res["network"] == current:
        network = f"`{current.relative_to(REPO_ROOT)}`"
    else:
        network = (f"a copy of `{current.name}`, "
                   + ("not the current network" if current.exists() and sha256(current) != log["input_sha256"]
                      else "identical to the current network"))
    out = [f"## {log['text']} ({label}), R = {log['r']}, seed {log['seed']}", "",
           f"Run folder `{res['run_dir'].relative_to(REPO_ROOT)}`; input network {network} "
           f"(SHA-256 `{log['input_sha256'][:16]}…`); repository commit `{log['repository commit'][:7]}`; "
           f"elapsed {int(log['elapsed seconds']) / 3600:.2f} h.", "",
           "- N_real equals the exact igraph/networkx count for all 13 triads.",
           "- Z recomputed from `_MAT_R.txt` " + ("agrees with mfinder's printed Z for every triad."
                                                  if not res["z_mismatch"] else
                                                  f"differs from mfinder's printed Z for IDs {res['z_mismatch']}."),
           f"- Flags: {'; '.join(flagged) if flagged else 'none'}.",
           f"- The smallest possible empirical p-value with R = {log['r']} is 1/{log['r'] + 1} = {min_p:.4f}.", "",
           "| ID | Triad | N_real | Null mean ± SD | Z | SE(Z) | p over | p under | q over | q under | "
           "Abundance ratio | Conc. | SP |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        out.append(f"| {r['mfinder_id']} | {r['triad_code']} | {r['n_real']:,} | {r['null_mean']:,.1f} ± "
                   f"{r['null_sd']:,.1f} | {r['z']:.2f}{star(r['z'], cfg['z_threshold'])} | {r['z_se']:.2f} | "
                   f"{r['p_over']:.4f} | {r['p_under']:.4f} | {r['q_over']:.4f} | {r['q_under']:.4f} | "
                   f"{r['abundance_ratio']:+.4f} | {r['concentration']:.4f} | {r['sp']:+.3f} |")
    over = [str(r["mfinder_id"]) for r in rows if r["q_over"] < cfg["fdr_alpha"]]
    under = [str(r["mfinder_id"]) for r in rows if r["q_under"] < cfg["fdr_alpha"]]
    out += ["", f"\\* |Z| > {cfg['z_threshold']}. With q < {cfg['fdr_alpha']}: over-represented "
                f"{', '.join(over) or 'none'}; under-represented {', '.join(under) or 'none'}.", ""]
    if log["text"] in old:
        cmp = old_vs_new(rows, old[log["text"]], cfg["z_threshold"])
        out += [f"### Old (Figure 9) vs new: {log['text']}", "",
                "Old: the Figure 9 run of Final Report 3 (R = 100): network built across sentence boundaries,",
                "from the old cleaning and pymorphy2 lemmas. New: this run. SP = Z / ||Z|| over the 13 triads.", "",
                f"Flip: the sign of Z, or whether \\|Z\\| > {cfg['z_threshold']}, differs between old and new.", "",
                "| ID | Triad | Old Z | New Z | ΔZ | Old SP | New SP | ΔSP | Flip |",
                "|---|---|---|---|---|---|---|---|---|"]
        for c in cmp:
            flips = [f for f, on in (("sign", c["sign_flip"]), ("significance", c["sig_flip"])) if on]
            out.append(f"| {c['mfinder_id']} | {c['triad_code']} | {c['old_z']:.2f}{star(c['old_z'], cfg['z_threshold'])} | "
                       f"{c['new_z']:.2f}{star(c['new_z'], cfg['z_threshold'])} | {c['dz']:+.2f} | {c['old_sp']:+.3f} | "
                       f"{c['new_sp']:+.3f} | {c['dsp']:+.3f} | {', '.join(flips)} |")
        out.append("")
    return out


def render_report(results: list[dict], config: dict, old: dict[str, dict[int, float]]) -> str:
    out = ["# Motif statistics report", "",
           "Generated by `uv run python -m huckfinn_cna.motifs`. Statistics recomputed from each mfinder run's",
           "per-random-network counts (`-orall`); definitions in the module docstring and `docs/decisions.md`.",
           ""]
    for res in results:
        out += render_run(res, config, old)
    return "\n".join(out).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_dirs", nargs="+", type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--network", type=Path, help="input network of the run (one run folder only)")
    parser.add_argument("--report", type=Path, help="report path (default: [paths].motif_report)")
    args = parser.parse_args()
    if args.network and len(args.run_dirs) != 1:
        parser.error("--network needs exactly one run folder")
    config = load_config(args.config)
    classes = config_classes(config)
    results = [analyse_run(d.resolve(), config, classes, args.network and args.network.resolve())
               for d in args.run_dirs]
    report = (args.report or repo_path(config, "motif_report")).resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(render_report(results, config, read_old_z(repo_path(config, "old_zscores"))), encoding="utf-8")
    for res in results:
        log = res["log"]
        print(f"{log['text']} R={log['r']}: N_real = igraph for all 13 triads; "
              f"Z vs mfinder: {'ok' if not res['z_mismatch'] else res['z_mismatch']}; "
              f"results in {res['run_dir'] / 'results.tsv'}")
    print(f"report: {report}")


if __name__ == "__main__":
    main()
