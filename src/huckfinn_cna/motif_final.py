"""Final motif analysis: the five r = 1000 runs, compared by significance-profile (SP) shape.

For each text, motifs.analyse_run gives N_real (checked against igraph), Z, empirical p-values in both
tails, BH q-values over the 13 triads, abundance ratio, concentration, SP and SE(Z), and writes
results.tsv into the run folder. Added here: checks of each run (exit status, complete run.log, input
SHA-256 against docs/networks/checksums.txt, no flags); the Ru1 r = 100 vs r = 1000 stability check; the
SP correlation matrix with average-linkage clustering on 1 - r; the motifs with the largest SP range;
old vs new against Figures 9-10 of Final Report 3, with sign and significance flips and a check of the
article's claims; greyscale replacements for Figures 9-11 and an SP correlation figure; and the
combined docs/motifs/motif_report.md.

Usage:
    uv run --frozen python -m huckfinn_cna.motif_final [--config PATH]
"""

from __future__ import annotations

import argparse
import csv
import math
from itertools import combinations
from pathlib import Path

import numpy as np

from huckfinn_cna.config import DEFAULT_CONFIG, REPO_ROOT, load_config, repo_path
from huckfinn_cna.motifs import analyse_run, read_old_z, sha256, significance_profile
from huckfinn_cna.networks import config_classes


class MotifFinalError(RuntimeError):
    pass


# --- Runs ------------------------------------------------------------------------------


def read_checksums(path: Path) -> dict[str, str]:
    """File name -> SHA-256 from a checksums file (comment and blank lines skipped)."""
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            digest, rel = line.split()
            out[Path(rel).name] = digest
    return out


def check_run_log(path: Path) -> dict:
    """Exit status 0 and start/end/elapsed present; returns those fields."""
    fields = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if ": " in line:
            k, v = line.split(": ", 1)
            fields[k.strip()] = v.strip()
    missing = [k for k in ("start", "end", "elapsed seconds", "exit status") if k not in fields]
    if missing:
        raise MotifFinalError(f"{path}: incomplete run.log, missing {missing}")
    if fields["exit status"] != "0":
        raise MotifFinalError(f"{path}: exit status {fields['exit status']}")
    return fields


def significant(row: dict, alpha: float) -> bool:
    """BH q-value in the direction of Z below alpha."""
    q = row["q_over"] if row["z"] > 0 else row["q_under"]
    return q < alpha


def read_results_tsv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    return [{k: (int(v) if k == "mfinder_id" else v if k in ("triad_code", "flags") else float(v)) for k, v in r.items()}
            for r in rows]


def analyse_runs(config: dict) -> dict:
    cfg, texts = config["motif_final"], config["corpus"]["texts"]
    classes = config_classes(config)
    checksums = read_checksums(repo_path(config, "network_checksums"))
    out = {}
    for t in texts:
        run_dir = repo_path(config, "mfinder_final_dir") / f"{t}_r{cfg['r']}"
        log_fields = check_run_log(run_dir / "run.log")
        res = analyse_run(run_dir, config, classes)
        name = f"{t}_mfinder.txt"
        if res["log"]["input_sha256"] != checksums.get(name):
            raise MotifFinalError(f"{t}: run input SHA-256 is not the networks-v1 checksum of {name}")
        if res["log"]["r"] != cfg["r"]:
            raise MotifFinalError(f"{t}: run.log R = {res['log']['r']}, expected {cfg['r']}")
        flags = [(r["mfinder_id"], r["flags"]) for r in res["rows"] if r["flags"]]
        if flags or res["z_mismatch"]:
            raise MotifFinalError(f"{t}: flags {flags}, Z mismatches {res['z_mismatch']}")
        res["run_log"] = log_fields
        out[t] = res
    return out


# --- Comparisons ---------------------------------------------------------------------------


def sp_matrix(results: dict, ids: list[int]) -> np.ndarray:
    return np.array([[next(r["sp"] for r in res["rows"] if r["mfinder_id"] == i) for i in ids] for res in results.values()])


def z_matrix(results: dict, ids: list[int], key: str = "z") -> np.ndarray:
    return np.array([[next(r[key] for r in res["rows"] if r["mfinder_id"] == i) for i in ids] for res in results.values()])


def correlation_matrix(x: np.ndarray) -> np.ndarray:
    return np.corrcoef(x)


def average_linkage_clades(corr: np.ndarray) -> tuple[np.ndarray, set[frozenset[int]]]:
    """Average linkage on 1 - r; returns the linkage matrix and its non-trivial clades."""
    from scipy.cluster.hierarchy import linkage
    from scipy.spatial.distance import squareform
    d = np.clip(1 - corr, 0, None)
    np.fill_diagonal(d, 0)
    link = linkage(squareform((d + d.T) / 2, checks=False), method="average")
    n = len(corr)
    members = {i: frozenset([i]) for i in range(n)}
    clades = set()
    for k, (a, b, _, _) in enumerate(link):
        members[n + k] = members[int(a)] | members[int(b)]
        if 1 < len(members[n + k]) < n:
            clades.add(members[n + k])
    return link, clades


def tree_string(clades: set[frozenset[int]], labels: list[str]) -> str:
    def build(members: frozenset) -> str:
        children, rest = [], set(members)
        for c in sorted((c for c in clades if c < members), key=len, reverse=True):
            if c <= rest and not any(c < o < members for o in clades):
                children.append(build(c))
                rest -= c
        return "(" + ", ".join(children + [labels[i] for i in sorted(rest)]) + ")"
    return build(frozenset(range(len(labels))))


def linkage_clades(d: np.ndarray) -> set[frozenset[int]]:
    """Non-trivial clades of average linkage on a distance matrix."""
    from scipy.cluster.hierarchy import linkage
    from scipy.spatial.distance import squareform
    n = len(d)
    link = linkage(squareform(d, checks=False), method="average")
    members = {i: frozenset([i]) for i in range(n)}
    out = set()
    for k, (a, b, _, _) in enumerate(link):
        members[n + k] = members[int(a)] | members[int(b)]
        if 1 < len(members[n + k]) < n:
            out.add(members[n + k])
    return out


def similarity_variants(sp: np.ndarray) -> dict[str, tuple[np.ndarray, set]]:
    """SP similarity under three measures and the average-linkage tree of each: Pearson r (primary),
    Spearman rho, Euclidean distance (matrix shown as distance)."""
    from scipy.stats import spearmanr
    pear = np.corrcoef(sp)
    spear = spearmanr(sp.T).correlation
    eu = np.linalg.norm(sp[:, None] - sp[None], axis=2)
    return {"Pearson r": (pear, linkage_clades(np.clip(1 - pear, 0, None) * (1 - np.eye(len(sp))))),
            "Spearman ρ": (spear, linkage_clades(np.clip(1 - spear, 0, None) * (1 - np.eye(len(sp))))),
            "Euclidean distance": (eu, linkage_clades(eu))}


def sp_ranges(sp: np.ndarray, ids: list[int]) -> list[tuple[int, float]]:
    """(mfinder ID, max - min of SP across texts), largest range first."""
    rng = sp.max(axis=0) - sp.min(axis=0)
    return sorted(zip(ids, rng.tolist()), key=lambda kv: -kv[1])


def stability(r100: list[dict], r1000: list[dict]) -> dict:
    """Ru1: Z difference relative to the combined SE, and the SP correlation."""
    a = {r["mfinder_id"]: r for r in r100}
    rows = []
    for r in r1000:
        o = a[r["mfinder_id"]]
        se = math.hypot(o["z_se"], r["z_se"])
        rows.append({"mfinder_id": r["mfinder_id"], "triad_code": r["triad_code"], "z100": o["z"], "z1000": r["z"],
                     "dz": r["z"] - o["z"], "se": se, "ratio": (r["z"] - o["z"]) / se, "sp100": o["sp"], "sp1000": r["sp"]})
    corr = float(np.corrcoef([x["sp100"] for x in rows], [x["sp1000"] for x in rows])[0, 1])
    return {"rows": rows, "sp_correlation": corr}


def claim_checks(old_sp: np.ndarray, new_sp: np.ndarray, old_z: np.ndarray, new_z: np.ndarray, ids: list[int],
                 texts: list[str], new_sig: np.ndarray, old_key: list[int]) -> list[dict]:
    """The article's Figure 9-11 claims, evaluated on the old values (were they true then?) and the new ones."""
    col = {i: k for k, i in enumerate(ids)}
    t = {name: k for k, name in enumerate(texts)}
    ru1, ru2, ru3, ru4, ru5 = (t[x] for x in texts[:5])

    def top(sp, motif):
        return texts[int(np.argmax(sp[:, col[motif]]))]

    def rank(sp, text, motif):
        return 1 + int((sp[:, col[motif]] > sp[t[text], col[motif]]).sum())

    def closest_pair(sp):
        cols = [col[m] for m in old_key]
        best = min(combinations(range(len(texts)), 2), key=lambda p: np.abs(sp[p[0], cols] - sp[p[1], cols]).sum())
        return texts[best[0]], texts[best[1]]

    out = []
    for name, sp, z in (("old", old_sp, old_z), ("new", new_sp, new_z)):
        out.append({
            "version": name,
            "braude_6_46": (top(sp, 6), top(sp, 46), top(z, 6) if name else None, top(z, 46)),
            "ru1_36": (sp[ru1, col[36]], rank(sp, texts[ru1], 36), z[ru1, col[36]]),
            "ru5_238": (top(sp, 238), sp[ru5, col[238]]),
            "ru4_vs_ru1_6": (sp[ru4, col[6]], sp[ru1, col[6]], top(sp, 6)),
            "ru2_12": (sp[ru2, col[12]], top(sp, 12)),
            "closest_key": closest_pair(sp),
            "ru3_ru5_key_rank": sorted(
                ((np.abs(sp[a, [col[m] for m in old_key]] - sp[b, [col[m] for m in old_key]]).sum(), (texts[a], texts[b]))
                 for a, b in combinations(range(len(texts)), 2))),
            "largest_range": sp_ranges(sp, ids)[:3],
        })
    return out


# --- Report ---------------------------------------------------------------------------------


def fz(x: float, d: int = 2) -> str:
    return f"{x:+.{d}f}".replace("+", "") if x >= 0 else f"−{abs(x):.{d}f}"


def render_report(config: dict, data: dict) -> str:
    cfgm, texts, labels = config["motifs"], list(data["results"]), config["corpus"]["labels"]
    ids, codes = data["ids"], data["codes"]
    alpha = cfgm["fdr_alpha"]
    res = data["results"]
    L = ["# Motif report", "",
         "Generated by `uv run --frozen python -m huckfinn_cna.motif_final` from the final mfinder runs "
         f"(`data/interim/mfinder/final/TEXT_r{config['motif_final']['r']}/`, R = {config['motif_final']['r']} random "
         "networks per text). Per-text statistics: `results.tsv` in each run folder (`src/huckfinn_cna/motifs.py`).",
         f"Significance: BH q < {alpha} over the 13 triads, in the direction of Z (empirical p = (b + 1)/(R + 1); the "
         f"smallest possible p is {1 / (config['motif_final']['r'] + 1):.4f}). SP = Z / ‖Z‖. SE(Z) ≈ √((1 + Z²/2)/R).", ""]

    L += ["## Runs", "", "| Text | Seed | Start | End | Elapsed | Exit | Input = networks-v1 | N_real = igraph | Z = mfinder | Flags |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for t, r in res.items():
        f = r["run_log"]
        L.append(f"| {t} | {r['log'].get('seed', '')} | {f['start']} | {f['end']} | {int(f['elapsed seconds']) / 3600:.1f} h | "
                 f"{f['exit status']} | yes | yes (13/13) | yes | none |")

    for key, title, d in (("z", "Z (BH q < 0.05 starred)", 2), ("sp", "Significance profile SP = Z/‖Z‖ (starred as Z)", 3)):
        L += ["", f"## {title}", "", "| Motif | Triad | " + " | ".join(texts) + " |", "|---|---|" + "---|" * len(texts)]
        for k, (i, c) in enumerate(zip(ids, codes)):
            cells = []
            for t in texts:
                row = next(x for x in res[t]["rows"] if x["mfinder_id"] == i)
                cells.append(fz(row[key], d) + ("*" if significant(row, alpha) else ""))
            L.append(f"| {i} | {c} | " + " | ".join(cells) + " |")

    L += ["", "## All statistics", "", "| Text | Motif | N_real | Null mean | Null SD | Z | SE(Z) | p over | p under | q over | "
          "q under | Abundance | Concentration | SP |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for t in texts:
        for r in res[t]["rows"]:
            L.append(f"| {t} | {r['mfinder_id']} | {r['n_real']:,} | {r['null_mean']:,.1f} | {r['null_sd']:,.1f} | {r['z']:.2f} | "
                     f"{r['z_se']:.3f} | {r['p_over']:.4f} | {r['p_under']:.4f} | {r['q_over']:.4f} | {r['q_under']:.4f} | "
                     f"{r['abundance_ratio']:.5f} | {r['concentration']:.4f} | {r['sp']:.3f} |")

    st = data["stability"]
    L += ["", "## Stability: Ru1, r = 100 (pre-Roman-numeral network) vs r = 1000", "",
          "The r = 100 run used the Ru1 network before Roman numerals were dropped (7 fewer distinct edges in the current "
          "network: 14 removed, 7 added). Combined SE = √(SE₁₀₀² + SE₁₀₀₀²).", "",
          "| Motif | Triad | Z (r=100) | Z (r=1000) | ΔZ | Combined SE | ΔZ / SE | SP (r=100) | SP (r=1000) |",
          "|---|---|---|---|---|---|---|---|---|"]
    for x in st["rows"]:
        L.append(f"| {x['mfinder_id']} | {x['triad_code']} | {x['z100']:.2f} | {x['z1000']:.2f} | {x['dz']:+.2f} | {x['se']:.2f} | "
                 f"{x['ratio']:+.2f} | {x['sp100']:.3f} | {x['sp1000']:.3f} |")
    L += ["", f"SP correlation (Pearson, 13 triads): {st['sp_correlation']:.4f}; largest |ΔZ / SE|: "
          f"{max(abs(x['ratio']) for x in st['rows']):.2f}."]

    corr = data["corr"]
    L += ["", "## SP correlation between translations", "", "Pearson r between the 13-triad SP vectors.", "",
          "| | " + " | ".join(texts) + " |", "|---|" + "---|" * len(texts)]
    for a, t in enumerate(texts):
        L.append(f"| {t} | " + " | ".join(f"{corr[a, b]:.3f}" for b in range(len(texts))) + " |")
    L += ["", f"Average linkage on 1 − r: **{tree_string(data['clades'], texts)}**.",
          "Nearest text by SP correlation: " + ", ".join(
              f"{t} → {texts[int(np.argmax(np.where(np.eye(len(texts), dtype=bool), -2, corr)[a]))]}" for a, t in enumerate(texts)) + "."]
    L += ["", "Robustness to the similarity measure (average linkage; correlations as 1 − value):", "",
          "| Measure | Tree | Most similar pair | Least similar pair |", "|---|---|---|---|"]
    for name, (mat, cl) in data["variants"].items():
        pairs = [(mat[a, b], texts[a], texts[b]) for a, b in combinations(range(len(texts)), 2)]
        best = (min if name.startswith("Euclidean") else max)(pairs)
        worst = (max if name.startswith("Euclidean") else min)(pairs)
        L.append(f"| {name} | {tree_string(cl, texts)} | {best[1]}–{best[2]} ({best[0]:.3f}) | {worst[1]}–{worst[2]} ({worst[0]:.3f}) |")
    sp_mat = data["variants"]["Spearman ρ"][0]
    L += ["", "Spearman ρ between SP vectors:", "", "| | " + " | ".join(texts) + " |", "|---|" + "---|" * len(texts)]
    for a, t in enumerate(texts):
        L.append(f"| {t} | " + " | ".join(f"{sp_mat[a, b]:.3f}" for b in range(len(texts))) + " |")
    L += ["", f"Old (Figure 10 SP, same method): **{tree_string(data['old_clades'], texts)}**.", "",
          "| | " + " | ".join(texts) + " |", "|---|" + "---|" * len(texts)]
    for a, t in enumerate(texts):
        L.append(f"| {t} | " + " | ".join(f"{data['old_corr'][a, b]:.3f}" for b in range(len(texts))) + " |")

    L += ["", "## SP range across translations", "", "| Motif | Triad | Range (new) | Min (text) | Max (text) | Range (old Figure 10) |",
          "|---|---|---|---|---|---|"]
    old_rng = dict(sp_ranges(data["old_sp"], ids))
    sp = data["sp"]
    for i, r in data["ranges"]:
        k = ids.index(i)
        L.append(f"| {i} | {codes[k]} | {r:.3f} | {sp[:, k].min():.3f} ({texts[int(np.argmin(sp[:, k]))]}) | "
                 f"{sp[:, k].max():.3f} ({texts[int(np.argmax(sp[:, k]))]}) | {old_rng[i]:.3f} |")
    L += ["", "Approximate SE of an SP value: SE(Z)/‖Z‖ (norm treated as fixed): " + ", ".join(
        f"{t} {np.median([r['z_se'] for r in res[t]['rows']]) / np.linalg.norm([r['z'] for r in res[t]['rows']]):.4f}"
        for t in texts) + " (median over triads)."]

    L += ["", "## Old vs new", "", "Old: Figure 9 (`input_OUT` run, R = 100; star = |Z| > 2) and Figure 10 (its SP). New: R = 1000; "
          "significance by BH q < 0.05 (and, for a like-for-like comparison, |Z| > 2).", ""]
    for t in texts:
        L += [f"### {t} ({labels[t]})", "", "| Motif | Triad | Old Z | New Z | Old SP | New SP | Sign flip | |Z|>2 flip | Old |Z|>2 → new q<0.05 |",
              "|---|---|---|---|---|---|---|---|---|"]
        for k, (i, c) in enumerate(zip(ids, codes)):
            row = next(x for x in res[t]["rows"] if x["mfinder_id"] == i)
            oz, nz = data["old_z"][texts.index(t), k], row["z"]
            L.append(f"| {i} | {c} | {oz:.2f}{'*' if abs(oz) > 2 else ''} | {nz:.2f}{'*' if significant(row, alpha) else ''} | "
                     f"{data['old_sp'][texts.index(t), k]:.3f} | {row['sp']:.3f} | {'yes' if (oz > 0) != (nz > 0) else ''} | "
                     f"{'yes' if (abs(oz) > 2) != (abs(nz) > 2) else ''} | "
                     f"{('sig' if abs(oz) > 2 else 'ns')} → {('sig' if significant(row, alpha) else 'ns')} |")
        L.append("")
    n_sign = sum((data["old_z"][a, k] > 0) != (data["z"][a, k] > 0) for a in range(len(texts)) for k in range(len(ids)))
    n_old_sig = int((np.abs(data["old_z"]) > 2).sum())
    n_new_sig = int(data["sig"].sum())
    L += [f"Totals over 65 cells: sign flips {n_sign}; old |Z| > 2: {n_old_sig}; new BH q < 0.05: {n_new_sig}; "
          f"new |Z| > 2: {int((np.abs(data['z']) > 2).sum())}.", ""]

    L += ["## The article's claims", ""]
    for c in data["claims"]:
        L += [f"### {c['version'].capitalize()} values", "",
              f"- Highest SP in motif 6: {c['braude_6_46'][0]}; in motif 46: {c['braude_6_46'][1]} (highest raw Z: motif 6 "
              f"{c['braude_6_46'][2]}, motif 46 {c['braude_6_46'][3]}).",
              f"- Ru1, motif 36: SP {c['ru1_36'][0]:.3f}, rank {c['ru1_36'][1]} of 5, Z {c['ru1_36'][2]:.2f}.",
              f"- Highest SP in motif 238: {c['ru5_238'][0]} (Ru5 SP {c['ru5_238'][1]:.3f}).",
              f"- Motif 6 SP: Ru4 {c['ru4_vs_ru1_6'][0]:.3f}, Ru1 {c['ru4_vs_ru1_6'][1]:.3f}; highest {c['ru4_vs_ru1_6'][2]}.",
              f"- Motif 12 SP: Ru2 {c['ru2_12'][0]:.3f}; highest {c['ru2_12'][1]}.",
              f"- Closest pair on motifs 6, 12, 36 (sum of |ΔSP|): {c['closest_key'][0]}–{c['closest_key'][1]}; Ru3–Ru5 rank "
              f"{1 + [p for _, p in c['ru3_ru5_key_rank']].index(('Ru3', 'Ru5'))} of 10.",
              f"- Largest SP range: " + ", ".join(f"{i} ({r:.3f})" for i, r in c["largest_range"]) + ".", ""]
    return "\n".join(L)


# --- Figures ------------------------------------------------------------------------------------


def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


def heatmap(config: dict, values: np.ndarray, sig: np.ndarray, ids: list[int], codes: list[str], texts: list[str],
            title: str, cbar_label: str, digits: int, path: Path) -> None:
    """Old Figure 9/10 style: Greys, white grid, annotated values, star = BH q < 0.05."""
    plt = _plt()
    fig, ax = plt.subplots(figsize=(14, 7.5))
    lim = np.abs(values).max()
    im = ax.imshow(values, cmap="Greys", vmin=-lim, vmax=lim, aspect="auto")
    for a in range(len(texts)):
        for k in range(len(ids)):
            v = values[a, k]
            ax.text(k, a, f"{v:.{digits}f}" + ("*" if sig[a, k] else ""), ha="center", va="center", fontsize=11,
                    color="white" if v > lim * 0.35 else "black")
    ax.set_xticks(range(len(ids)), [f"{i}\n{c}" for i, c in zip(ids, codes)], fontsize=10)
    ax.set_yticks(range(len(texts)), [config["corpus"]["labels"][t] for t in texts], fontsize=11)
    ax.set_xticks(np.arange(-.5, len(ids)), minor=True)
    ax.set_yticks(np.arange(-.5, len(texts)), minor=True)
    ax.grid(which="minor", color="white", linewidth=1)
    ax.tick_params(which="both", length=0)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_xlabel("Motif (mfinder ID, triad code)", fontsize=12)
    ax.set_ylabel("Translation", fontsize=12)
    ax.set_title(title, fontsize=15, pad=14)
    cbar = fig.colorbar(im, ax=ax, fraction=0.03, pad=0.02)
    cbar.set_label(cbar_label)
    cbar.outline.set_visible(False)
    fig.tight_layout()
    fig.savefig(path, dpi=config["pos_check"]["figure_dpi"], bbox_inches="tight")
    plt.close(fig)


def grouped_bars(config: dict, sp: np.ndarray, sig: np.ndarray, motifs: list[int], ids: list[int], codes: list[str],
                 texts: list[str], old_key: list[int], path: Path) -> None:
    """Old Figure 11 style grouped bars (greys light to dark by translation), labelled values."""
    plt = _plt()
    shades = ["#333333", "#666666", "#999999", "#c4c4c4", "#e8e8e8"]
    fig, ax = plt.subplots(figsize=(15, 7.5))
    width = 0.8 / len(texts)
    x = np.arange(len(motifs))
    for a, t in enumerate(texts):
        vals = [sp[a, ids.index(m)] for m in motifs]
        bars = ax.bar(x - 0.4 + width * (a + 0.5), vals, width, color=shades[a % len(shades)], edgecolor="black",
                      label=config["corpus"]["labels"][t])
        ax.bar_label(bars, labels=[f"{v:.2f}" + ("*" if sig[a, ids.index(m)] else "") for v, m in zip(vals, motifs)],
                     padding=3, fontsize=8, rotation=90)
    names = []
    for m in motifs:
        tag = " (old key)" if m in old_key else ""
        names.append(f"Motif {m}\n{codes[ids.index(m)]}{tag}")
    ax.set_xticks(x, names, fontsize=11)
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_ylabel("Significance profile (Z / ‖Z‖)", fontsize=12)
    ax.set_xlabel("Motif: largest SP range across translations, then the old key motifs", fontsize=12)
    ax.set_title("Key Structural Preferences by Translator", fontsize=16)
    ax.legend(title="Translation", frameon=True, fontsize=10, loc="center left", bbox_to_anchor=(1.01, 0.5))
    lo, hi = sp[:, [ids.index(m) for m in motifs]].min(), sp[:, [ids.index(m) for m in motifs]].max()
    ax.set_ylim(min(0, lo) * 1.3 - 0.03, hi * 1.25)
    fig.tight_layout()
    fig.savefig(path, dpi=config["pos_check"]["figure_dpi"], bbox_inches="tight")
    plt.close(fig)


def correlation_figure(config: dict, corr: np.ndarray, link: np.ndarray, texts: list[str], path: Path) -> None:
    from scipy.cluster.hierarchy import dendrogram
    plt = _plt()
    labels = [config["corpus"]["labels"][t] for t in texts]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(18, 7.5), gridspec_kw={"width_ratios": [1.15, 1]})
    off = corr[~np.eye(len(corr), dtype=bool)]
    im = ax1.imshow(corr, cmap="Greys", vmin=off.min(), vmax=1)
    for a in range(len(corr)):
        for b in range(len(corr)):
            ax1.text(b, a, f"{corr[a, b]:.3f}", ha="center", va="center", fontsize=11,
                     color="white" if corr[a, b] > (off.min() + 1) / 2 else "black")
    ax1.set_xticks(range(len(corr)), labels, rotation=90)
    ax1.set_yticks(range(len(corr)), labels)
    ax1.set_xticks(np.arange(-.5, len(corr)), minor=True)
    ax1.set_yticks(np.arange(-.5, len(corr)), minor=True)
    ax1.grid(which="minor", color="black", linewidth=0.5)
    ax1.tick_params(which="both", length=0)
    fig.colorbar(im, ax=ax1, fraction=0.046, pad=0.04)
    ax1.set_title("Correlation of significance profiles", fontsize=14)
    dendrogram(link, labels=labels, orientation="right", leaf_font_size=12, link_color_func=lambda k: "black", ax=ax2)
    ax2.set_title("Average linkage on 1 − r", fontsize=14)
    ax2.set_xlabel("1 − r", fontsize=12)
    for s in ("top", "right"):
        ax2.spines[s].set_visible(False)
    fig.suptitle("Motif significance profiles compared across translations (R = 1000)", fontsize=16)
    fig.tight_layout()
    fig.savefig(path, dpi=config["pos_check"]["figure_dpi"], bbox_inches="tight")
    plt.close(fig)


# --- Run ---------------------------------------------------------------------------------------


def build(config: dict) -> dict:
    texts = config["corpus"]["texts"]
    classes = config_classes(config)
    ids, codes = [c["mfinder_id"] for c in classes], [c["triad_code"] for c in classes]
    results = analyse_runs(config)
    sp, z = sp_matrix(results, ids), z_matrix(results, ids)
    alpha = config["motifs"]["fdr_alpha"]
    sig = np.array([[significant(next(r for r in results[t]["rows"] if r["mfinder_id"] == i), alpha) for i in ids] for t in texts])
    old = read_old_z(repo_path(config, "old_zscores"))
    old_z = np.array([[old[t][i] for i in ids] for t in texts])
    old_sp = np.array([significance_profile(list(row)) for row in old_z])
    corr, old_corr = correlation_matrix(sp), correlation_matrix(old_sp)
    link, clades = average_linkage_clades(corr)
    _, old_clades = average_linkage_clades(old_corr)
    r100 = read_results_tsv(REPO_ROOT / config["motif_final"]["stability_run"] / "results.tsv")
    return {"results": results, "ids": ids, "codes": codes, "sp": sp, "z": z, "sig": sig, "old_z": old_z, "old_sp": old_sp,
            "corr": corr, "old_corr": old_corr, "link": link, "clades": clades, "old_clades": old_clades,
            "ranges": sp_ranges(sp, ids), "variants": similarity_variants(sp), "stability": stability(r100, results[texts[0]]["rows"]),
            "claims": claim_checks(old_sp, sp, old_z, z, ids, texts, sig, config["motif_final"]["old_key_motifs"])}


def run(config_path: Path = DEFAULT_CONFIG) -> dict:
    config = load_config(config_path)
    data = build(config)
    texts, cfg = config["corpus"]["texts"], config["motif_final"]
    report = repo_path(config, "motif_report")
    report.write_text(render_report(config, data) + "\n", encoding="utf-8")
    out = repo_path(config, "figures_dir")
    heatmap(config, data["z"], data["sig"], data["ids"], data["codes"], texts,
            "Network Motif Raw Z-Scores (R = 1000)\n* = significant (Benjamini–Hochberg q < 0.05, empirical p-values)",
            "Z-score", 1, out / "fig9_motif_z_heatmap_greyscale.png")
    heatmap(config, data["sp"], data["sig"], data["ids"], data["codes"], texts,
            "Network Motif Significance Profile (Z / ‖Z‖)\n* = significant (Benjamini–Hochberg q < 0.05)",
            "Significance profile (vector length = 1)", 2, out / "fig10_motif_sp_heatmap_greyscale.png")
    motifs = [i for i, _ in data["ranges"][:cfg["top_range"]]]
    motifs += [m for m in cfg["old_key_motifs"] if m not in motifs]
    grouped_bars(config, data["sp"], data["sig"], motifs, data["ids"], data["codes"], texts, cfg["old_key_motifs"],
                 out / "fig11_key_motifs_sp_greyscale.png")
    correlation_figure(config, data["corr"], data["link"], texts, out / "motif_sp_correlation_greyscale.png")
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    data = run(args.config)
    texts = list(data["results"])
    print("SP tree:", tree_string(data["clades"], texts), "| old:", tree_string(data["old_clades"], texts))
    print("largest SP range:", data["ranges"][:3])
    print("Ru1 r100 vs r1000 SP r =", round(data["stability"]["sp_correlation"], 4))


if __name__ == "__main__":
    main()
