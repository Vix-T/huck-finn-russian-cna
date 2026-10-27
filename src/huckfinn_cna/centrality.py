"""Betweenness centrality of the frozen word-adjacency networks (networks-v1).

Variants: exact unweighted betweenness on the undirected network (primary), on the directed network
(secondary), and directed with distance = 1 / bigram count (the old method, sensitivity); all
normalized as networkx does. igraph is cross-checked against networkx on the smallest network.
Frequency adjustment (Amancio 2015): each text's word tokens are shuffled across the text with every
sentence's length kept, the network rebuilt with the same rules, and the undirected betweenness of
the real network divided by its mean over the shuffles (also a z-score). Old vs new: the old
rankings (Final Report 3, Figure 12) are read from the old repository (read-only) and the old method
is re-run on the old networks to check they reproduce.

Writes docs/centrality/centrality_report.md (tables only), data/interim/centrality/Ru#_betweenness.tsv
and greyscale replacements for Figure 12 in docs/amendment/figures/.

Usage:
    uv run --frozen python -m huckfinn_cna.centrality [--config PATH]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

import numpy as np

from huckfinn_cna.config import DEFAULT_CONFIG, REPO_ROOT, load_config, repo_path
from huckfinn_cna.networks import count_edges, sentence_lemmas
from huckfinn_cna.tag import read_tagged

VARIANT_LABELS = {
    "undirected": "Undirected, unweighted",
    "directed": "Directed, unweighted",
    "weighted": "Directed, distance 1/count (old method)",
    "adjusted": "Frequency-adjusted (undirected)",
}


class CentralityError(RuntimeError):
    pass


# --- Inputs ----------------------------------------------------------------------


def check_network_checksums(config: dict) -> dict[str, str]:
    """SHA-256 of every network listed in the networks-v1 checksum file; fails on any mismatch."""
    out = {}
    for line in repo_path(config, "network_checksums").read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        digest, rel = line.split()
        actual = hashlib.sha256((REPO_ROOT / rel).read_bytes()).hexdigest()
        if actual != digest:
            raise CentralityError(f"{rel} does not match networks-v1 ({actual} != {digest}); rebuild or re-freeze")
        out[rel] = digest
    return out


def read_tsv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh, delimiter="\t", quoting=csv.QUOTE_NONE, quotechar=None))


def load_network(net_dir: Path, text_id: str) -> tuple[list[str], Counter]:
    """Node labels (index = id - 1) and edge counts from Ru#_nodes.tsv / Ru#_edges.tsv, checked against the
    checksummed Ru#_mfinder.txt (same edge set)."""
    nodes = read_tsv(net_dir / f"{text_id}_nodes.tsv")
    labels = [r["lemma"] for r in sorted(nodes, key=lambda r: int(r["id"]))]
    if [int(r["id"]) for r in sorted(nodes, key=lambda r: int(r["id"]))] != list(range(1, len(labels) + 1)):
        raise CentralityError(f"{text_id}: node ids are not 1..n")
    edges = Counter({(r["source"], r["target"]): int(r["count"]) for r in read_tsv(net_dir / f"{text_id}_edges.tsv")})
    index = {lab: i + 1 for i, lab in enumerate(labels)}
    mf = {tuple(map(int, line.split()[:2])) for line in (net_dir / f"{text_id}_mfinder.txt").read_text().splitlines() if line}
    if {(index[a], index[b]) for a, b in edges} != mf:
        raise CentralityError(f"{text_id}: Ru#_edges.tsv and Ru#_mfinder.txt differ")
    return labels, edges


def text_sentences(config: dict, text_id: str) -> list[list[str]]:
    rows = read_tagged(repo_path(config, "tagged_dir") / f"{text_id}.tsv")
    return sentence_lemmas(rows, config["networks"]["lemma_char_map"])


# --- Betweenness ------------------------------------------------------------------


def normalization(n: int, directed: bool) -> float:
    """networkx's normalization: 1 / ((n-1)(n-2)), times 2 for undirected graphs."""
    if n < 3:
        return 1.0
    scale = 1 / ((n - 1) * (n - 2))
    return scale if directed else 2 * scale


def betweenness(labels: list[str], edges: Counter, variant: str) -> np.ndarray:
    """Normalized exact betweenness of every node (order of `labels`) for one variant."""
    import igraph as ig
    index = {lab: i for i, lab in enumerate(labels)}
    pairs = list(edges)
    g = ig.Graph(n=len(labels), edges=[(index[a], index[b]) for a, b in pairs], directed=True)
    if variant == "undirected":
        g = g.as_undirected(mode="collapse")
        raw = g.betweenness(directed=False)
        directed = False
    elif variant == "directed":
        raw = g.betweenness(directed=True)
        directed = True
    elif variant == "weighted":
        raw = g.betweenness(directed=True, weights=[1 / edges[p] for p in pairs])
        directed = True
    else:
        raise ValueError(f"unknown variant {variant!r}")
    return np.asarray(raw, dtype=float) * normalization(len(labels), directed)


def networkx_undirected(labels: list[str], edges: Counter) -> np.ndarray:
    import networkx as nx
    g = nx.Graph()
    g.add_nodes_from(labels)
    g.add_edges_from(edges)
    bc = nx.betweenness_centrality(g, normalized=True)
    return np.array([bc[lab] for lab in labels])


def shuffle_sentences(sentences: list[list[str]], rng) -> list[list[str]]:
    """All word tokens permuted across the text, refilled into sentences of the original lengths."""
    flat = [w for s in sentences for w in s]
    perm = rng.permutation(len(flat))
    out, pos = [], 0
    for s in sentences:
        out.append([flat[i] for i in perm[pos:pos + len(s)]])
        pos += len(s)
    return out


def shuffled_betweenness(labels: list[str], sentences: list[list[str]], k: int, rng) -> np.ndarray:
    """k × nodes array of undirected unweighted betweenness of shuffled networks."""
    out = np.empty((k, len(labels)))
    for i in range(k):
        edges, _ = count_edges(shuffle_sentences(sentences, rng))
        out[i] = betweenness(labels, edges, "undirected")
    return out


def frequent_mask(freq: np.ndarray, top: int) -> np.ndarray:
    """Words at least as frequent as the top-th most frequent word (ties at the boundary included)."""
    if top >= len(freq):
        return np.ones(len(freq), dtype=bool)
    return freq >= np.sort(freq)[::-1][top - 1]


def adjust(real: np.ndarray, shuffled: np.ndarray, ddof: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(mean, sd, real / mean, (real - mean) / sd); NaN where the denominator is 0."""
    mean, sd = shuffled.mean(axis=0), shuffled.std(axis=0, ddof=ddof)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(mean > 0, real / mean, np.nan)
        z = np.where(sd > 0, (real - mean) / sd, np.nan)
    return mean, sd, ratio, z


# --- Ranks and correlation ------------------------------------------------------------


def competition_ranks(scores: np.ndarray) -> np.ndarray:
    """1 = highest; ties share the best rank; NaN scores get NaN."""
    ranks = np.full(len(scores), np.nan)
    valid = ~np.isnan(scores)
    vals = scores[valid]
    order = np.sort(vals)[::-1]
    ranks[valid] = np.searchsorted(-order, -vals, side="left") + 1
    return ranks


def average_ranks(x: np.ndarray) -> np.ndarray:
    """Ranks 1..n (ascending), ties averaged."""
    order = np.argsort(x, kind="stable")
    ranks = np.empty(len(x))
    sx = x[order]
    i = 0
    while i < len(x):
        j = i
        while j + 1 < len(x) and sx[j + 1] == sx[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def pearson(x: np.ndarray, y: np.ndarray) -> float:
    return float(np.corrcoef(x, y)[0, 1])


def spearman(x: np.ndarray, y: np.ndarray) -> float:
    return pearson(average_ranks(x), average_ranks(y))


def top_words(labels: list[str], scores: np.ndarray, n: int) -> list[str]:
    """n highest-scoring words (NaN excluded), ties broken alphabetically."""
    idx = [i for i in range(len(labels)) if not np.isnan(scores[i])]
    idx.sort(key=lambda i: (-scores[i], labels[i]))
    return [labels[i] for i in idx[:n]]


def stability(labels: list[str], a: np.ndarray, b: np.ndarray, n: int) -> tuple[float, int]:
    """Spearman correlation of the full ranks in a and b over the union of their top-n words."""
    index = {lab: i for i, lab in enumerate(labels)}
    words = sorted(set(top_words(labels, a, n)) | set(top_words(labels, b, n)))
    ra, rb = competition_ranks(a), competition_ranks(b)
    pairs = [(ra[index[w]], rb[index[w]]) for w in words if not (np.isnan(ra[index[w]]) or np.isnan(rb[index[w]]))]
    x, y = np.array(pairs).T
    return spearman(x, y), len(pairs)


# --- Old method ---------------------------------------------------------------------------


def old_dir(config: dict) -> Path:
    return Path(config["paths"]["old_repo"]) / config["paths"]["old_centrality_dir"]


def old_rankings(config: dict, text_id: str) -> dict[str, tuple[int, float]]:
    """Old Ru#_betweenness_rankings.csv: word -> (rank = row number, as the old heatmap used, score)."""
    with (old_dir(config) / f"{text_id}_betweenness_rankings.csv").open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    return {r["Word"]: (i, float(r["Betweenness"])) for i, r in enumerate(rows, start=1)}


def old_network(config: dict, text_id: str) -> tuple[list[str], Counter]:
    """Old mfinder input (source id, target id, bigram count) as labels and edge counts. Like the old script,
    only nodes that appear in an edge are included."""
    mapping = json.loads((old_dir(config) / f"{text_id}_word_id_mapping.json").read_text(encoding="utf-8"))
    word = {int(v): k for k, v in mapping.items()}
    edges = Counter()
    for line in (old_dir(config) / f"{text_id}_mfinder_input.txt").read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 3:
            edges[word[int(parts[0])], word[int(parts[1])]] = int(parts[2])
    labels = sorted({w for pair in edges for w in pair})
    return labels, edges


# --- Per text --------------------------------------------------------------------------------


def analyse_text(config: dict, text_index: int, text_id: str) -> dict:
    cfg = config["centrality"]
    labels, edges = load_network(repo_path(config, "networks_dir"), text_id)
    sentences = text_sentences(config, text_id)
    rebuilt, _ = count_edges(sentences)
    if rebuilt != edges or sorted({w for s in sentences for w in s}) != labels:
        raise CentralityError(f"{text_id}: tagged file no longer rebuilds the frozen network")
    freq = Counter(w for s in sentences for w in s)
    scores = {v: betweenness(labels, edges, v) for v in cfg["variants"]}
    rng = np.random.default_rng([cfg["master_seed"], text_index])
    shuffled = shuffled_betweenness(labels, sentences, cfg["shuffles"], rng)
    mean, sd, ratio, z = adjust(scores[cfg["primary"]], shuffled, cfg["shuffle_sd_ddof"])
    f = np.array([freq[w] for w in labels], dtype=float)
    eligible = frequent_mask(f, cfg["adjusted_rank_top_frequent"])
    scores["adjusted"] = np.where(eligible, ratio, np.nan)     # ranked score: frequent lemmas only

    old = old_rankings(config, text_id)
    old_labels, old_edges = old_network(config, text_id)
    replication = betweenness(old_labels, old_edges, "weighted")
    old_scores = np.array([old[w][1] for w in old_labels])
    rep_ranks = competition_ranks(replication)
    old_unweighted = betweenness(old_labels, old_edges, "undirected")
    return {
        "labels": labels, "freq": f, "scores": scores, "shuffle_mean": mean, "shuffle_sd": sd, "z": z,
        "adjusted_all": ratio, "eligible": int(eligible.sum()), "eligible_min_freq": int(f[eligible].min()),
        "ranks": {v: competition_ranks(s) for v, s in scores.items()},
        "n_nodes": len(labels), "n_edges": len(edges),
        "n_undirected_edges": len({tuple(sorted(p)) for p in edges}),
        "log_freq_corr": {v: {"pearson": pearson(scores[v], np.log10(f)), "spearman": spearman(scores[v], f)}
                          for v in cfg["variants"]},
        "adjusted_undefined": int(np.isnan(ratio).sum()),
        "old": old,
        "old_replication": {
            "nodes": len(old_labels), "max_abs_diff": float(np.max(np.abs(replication - old_scores))),
            "top100_same": all(rep_ranks[i] == old[w][0] for i, w in enumerate(old_labels) if old[w][0] <= 100),
        },
        "old_unweighted_ranks": dict(zip(old_labels, competition_ranks(old_unweighted))),
    }


# --- Output ----------------------------------------------------------------------------------


def write_scores(path: Path, r: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = ["undirected", "directed", "weighted", "adjusted"]
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(["word", "frequency"] + cols + ["shuffle_mean", "shuffle_sd", "z"] + [f"rank_{c}" for c in cols])
        for i, lab in enumerate(r["labels"]):
            values = [r["adjusted_all"][i] if c == "adjusted" else r["scores"][c][i] for c in cols]
            w.writerow([lab, int(r["freq"][i])] + [f"{x:.10g}" for x in values]
                       + [f"{r['shuffle_mean'][i]:.10g}", f"{r['shuffle_sd'][i]:.10g}", f"{r['z'][i]:.6g}"]
                       + ["" if np.isnan(r["ranks"][c][i]) else int(r["ranks"][c][i]) for c in cols])


def old_label_map(word: str, config: dict) -> str:
    """An old label as a new node label (the old lemmas can contain ё, e.g. «всё»)."""
    return "".join(config["networks"]["lemma_char_map"].get(ch, ch) for ch in word)


def rank_of(r: dict, variant: str, word: str):
    try:
        x = r["ranks"][variant][r["labels"].index(word)]
    except ValueError:
        return None
    return None if np.isnan(x) else int(x)


def heatmap_columns(rank_maps: dict[str, dict[str, int]], cfg: dict) -> list[str]:
    """Old Figure 12 rule: union of each text's top-N, sorted by mean rank (absent = penalty), first M kept."""
    words = {w for ranks in rank_maps.values() for w, k in ranks.items() if k <= cfg["heatmap_top_n"]}
    def mean_rank(w):
        return sum(ranks.get(w, cfg["heatmap_absent_rank"]) for ranks in rank_maps.values()) / len(rank_maps)
    return sorted(words, key=lambda w: (mean_rank(w), w))[:cfg["heatmap_max_columns"]]


def new_rank_maps(results: dict, variant: str) -> dict[str, dict[str, int]]:
    return {t: {lab: int(k) for lab, k in zip(r["labels"], r["ranks"][variant]) if not np.isnan(k)}
            for t, r in results.items()}


def old_rank_maps(results: dict) -> dict[str, dict[str, int]]:
    return {t: {w: k for w, (k, _) in r["old"].items()} for t, r in results.items()}


def fmt_rank(x) -> str:
    return "—" if x is None else str(x)


def render_report(config: dict, results: dict, crosscheck: dict, checksums: dict) -> str:
    cfg, labels, ids = config["centrality"], config["corpus"]["labels"], list(results)
    variants = cfg["variants"] + ["adjusted"]
    L = ["# Betweenness centrality", "",
         "Generated by `uv run --frozen python -m huckfinn_cna.centrality` from the frozen networks (networks-v1).",
         "Tables only. Betweenness is exact (no sampling) and normalized as networkx does. Ranks: 1 = highest; ties share the best rank.", "",
         "Variants: " + "; ".join(f"**{v}** = {VARIANT_LABELS[v]}" for v in variants) + ".",
         f"Frequency adjustment: {cfg['shuffles']} shuffles per text (word tokens permuted across the text, sentence lengths kept, "
         f"network rebuilt with the same rules); adjusted = real / mean shuffled undirected betweenness; z = (real − mean) / SD "
         f"(ddof {cfg['shuffle_sd_ddof']}). Seed: numpy `default_rng([{cfg['master_seed']}, text index])`.", "",
         "## Inputs", "", "Network files match networks-v1:", ""]
    L += [f"- `{rel}` `{d[:16]}…`" for rel, d in checksums.items()]
    L += ["", f"Adjusted ranks use only each text's {cfg['adjusted_rank_top_frequent']} most frequent lemmas (ties at the boundary "
          "included); adjusted scores and z for every word are in `data/interim/centrality/Ru#_betweenness.tsv`.", "",
          "| Text | Nodes | Directed edges | Undirected edges | Adjusted score undefined (shuffle mean 0) | Words ranked by adjusted score | Their minimum frequency |",
          "|---|---|---|---|---|---|---|"]
    for t in ids:
        r = results[t]
        L.append(f"| {t} | {r['n_nodes']:,} | {r['n_edges']:,} | {r['n_undirected_edges']:,} | {r['adjusted_undefined']} | "
                 f"{r['eligible']} | {r['eligible_min_freq']} |")

    L += ["", "## Cross-check: igraph vs networkx", "",
          f"{cfg['crosscheck_text']}, undirected unweighted, normalized: max |difference| = {crosscheck['max_abs_diff']:.2e} over "
          f"{crosscheck['nodes']:,} nodes (tolerance {cfg['crosscheck_abs_tol']:g}): {'agree' if crosscheck['agree'] else 'DISAGREE'}."]

    L += ["", "## Replication of the old rankings", "",
          "The old method (directed, distance 1/count) re-run with igraph on the old networks (`Ru#_mfinder_input.txt`, nodes in an "
          "edge only, as in the old script) against the old `Ru#_betweenness_rankings.csv`.", "",
          "| Text | Nodes | Max |score difference| | Top-100 ranks identical |", "|---|---|---|---|"]
    for t in ids:
        o = results[t]["old_replication"]
        L.append(f"| {t} | {o['nodes']:,} | {o['max_abs_diff']:.2e} | {'yes' if o['top100_same'] else 'no'} |")

    L += ["", "## Betweenness and log frequency", "",
          "Pearson r between betweenness and log10 frequency, and Spearman ρ between betweenness and frequency, over all nodes.", "",
          "| Text | " + " | ".join(f"{VARIANT_LABELS[v]}: r | ρ" for v in cfg["variants"]) + " |",
          "|---|" + "---|" * len(cfg["variants"])]
    for t in ids:
        c = results[t]["log_freq_corr"]
        L.append(f"| {t} | " + " | ".join(f"{c[v]['pearson']:.3f} / {c[v]['spearman']:.3f}" for v in cfg["variants"]) + " |")

    L += ["", f"## Rank stability (Spearman ρ over the union of both variants' top {cfg['stability_top_n']})", "",
          "| Text | " + " | ".join(f"{a} vs {b}" for a, b in pairs(variants)) + " |", "|---|" + "---|" * len(pairs(variants))]
    for t in ids:
        r = results[t]
        cells = []
        for a, b in pairs(variants):
            rho, n = stability(r["labels"], r["scores"][a], r["scores"][b], cfg["stability_top_n"])
            cells.append(f"{rho:.3f} (n={n})")
        L.append(f"| {t} | " + " | ".join(cells) + " |")

    focus = cfg["focus_words"]
    L += ["", "## Focus words: old rank vs new ranks", "",
          "Old: row number in the old `Ru#_betweenness_rankings.csv` (directed, distance 1/count, old network). "
          "*Old net, unweighted*: undirected unweighted betweenness of the old network.", "",
          "| Word | Text | Old | Old net, unweighted | " + " | ".join(VARIANT_LABELS[v] for v in variants)
          + " | Adjusted score | z | Frequency |",
          "|---|---|---|---|" + "---|" * len(variants) + "---|---|---|"]
    for w in focus:
        for t in ids:
            r = results[t]
            old = r["old"].get(w)
            ou = r["old_unweighted_ranks"].get(w)
            j = r["labels"].index(w) if w in r["labels"] else None
            f = int(r["freq"][j]) if j is not None else 0
            adj = "—" if j is None else f"{r['adjusted_all'][j]:.2f}"
            z = "—" if j is None else f"{r['z'][j]:+.1f}"
            L.append(f"| {w} | {t} | {fmt_rank(old[0] if old else None)} | {fmt_rank(None if ou is None else int(ou))} | "
                     + " | ".join(fmt_rank(rank_of(r, v, w)) for v in variants) + f" | {adj} | {z} | {f:,} |")

    cols = heatmap_columns(old_rank_maps(results), cfg)
    for v in ["undirected", "weighted", "adjusted"]:
        L += ["", f"## Old Figure 12 words: old rank → new rank ({VARIANT_LABELS[v]})", "",
              "| Word | " + " | ".join(ids) + " |", "|---|" + "---|" * len(ids)]
        for w in cols:
            nw = old_label_map(w, config)
            cells = []
            for t in ids:
                old = results[t]["old"].get(w)
                cells.append(f"{fmt_rank(old[0] if old else None)} → {fmt_rank(rank_of(results[t], v, nw))}")
            L.append(f"| {w}{' (' + nw + ')' if nw != w else ''} | " + " | ".join(cells) + " |")

    for v in ["undirected", "adjusted"]:
        L += ["", f"## Top {cfg['top_n']} words per text: {VARIANT_LABELS[v]}", "",
              "| Rank | " + " | ".join(ids) + " |", "|---|" + "---|" * len(ids)]
        tops = {t: top_words(results[t]["labels"], results[t]["scores"][v], cfg["top_n"]) for t in ids}
        for i in range(cfg["top_n"]):
            cells = []
            for t in ids:
                r = results[t]
                w = tops[t][i]
                j = r["labels"].index(w)
                extra = f" ({r['scores'][v][j]:.2f}, f={int(r['freq'][j])})" if v == "adjusted" else f" ({r['scores'][v][j]:.4f})"
                cells.append(w + extra)
            L.append(f"| {i + 1} | " + " | ".join(cells) + " |")
    return "\n".join(L)


def pairs(items: list[str]) -> list[tuple[str, str]]:
    return [(a, b) for i, a in enumerate(items) for b in items[i + 1:]]


def rank_heatmap(config: dict, rank_maps: dict[str, dict[str, int]], path: Path, subtitle: str) -> list[str]:
    """Greyscale rank heatmap in the layout of the old Figure 12 (rank 1 = black)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    cfg, ids = config["centrality"], list(rank_maps)
    cols = heatmap_columns(rank_maps, cfg)
    grid = np.array([[rank_maps[t].get(w, np.nan) for w in cols] for t in ids], dtype=float)
    vmax = cfg["heatmap_color_max"]
    fig, ax = plt.subplots(figsize=(20, 8))
    im = ax.imshow(np.clip(grid, 1, vmax), cmap="Greys_r", vmin=1, vmax=vmax, aspect="auto")
    for i in range(len(ids)):
        for j in range(len(cols)):
            v = grid[i, j]
            ax.text(j, i, "—" if np.isnan(v) else f"{v:.0f}", ha="center", va="center", fontsize=11,
                    color="black" if np.isnan(v) or min(v, vmax) > vmax * 0.55 else "white")
    ax.set_xticks(range(len(cols)), cols, rotation=45, ha="right", fontsize=11)
    ax.set_yticks(range(len(ids)), [config["corpus"]["labels"][t] for t in ids], fontsize=11)
    ax.set_xticks(np.arange(-.5, len(cols)), minor=True)
    ax.set_yticks(np.arange(-.5, len(ids)), minor=True)
    ax.grid(which="minor", color="white", linewidth=1)
    ax.tick_params(which="both", length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_title(f"Comparative Rank of Top Bridge Words\nBetweenness Centrality Ranking ({subtitle})", fontsize=16, pad=20)
    ax.set_ylabel("Translation", fontsize=12)
    ax.set_xlabel("Bridge Words (Sorted by Consistency)", fontsize=12)
    cbar = fig.colorbar(im, ax=ax, fraction=0.03, pad=0.02)
    cbar.set_label(f"Betweenness Rank (1 = Most Central; scale capped at {vmax})", fontsize=10)
    cbar.outline.set_visible(False)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=config["pos_check"]["figure_dpi"], bbox_inches="tight")
    plt.close(fig)
    return cols


def run(config_path: Path = DEFAULT_CONFIG) -> dict:
    config = load_config(config_path)
    cfg = config["centrality"]
    checksums = check_network_checksums(config)
    results = {t: analyse_text(config, i, t) for i, t in enumerate(config["corpus"]["texts"])}

    ref = results[cfg["crosscheck_text"]]
    labels, edges = load_network(repo_path(config, "networks_dir"), cfg["crosscheck_text"])
    nx_scores = networkx_undirected(labels, edges)
    diff = float(np.max(np.abs(nx_scores - ref["scores"]["undirected"])))
    crosscheck = {"nodes": len(labels), "max_abs_diff": diff, "agree": diff <= cfg["crosscheck_abs_tol"]}
    if not crosscheck["agree"]:
        raise CentralityError(f"igraph and networkx disagree on {cfg['crosscheck_text']}: {diff:.3e}")

    out_dir = repo_path(config, "centrality_results")
    for t, r in results.items():
        write_scores(out_dir / f"{t}_betweenness.tsv", r)
    report = repo_path(config, "centrality_report")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(render_report(config, results, crosscheck, checksums) + "\n", encoding="utf-8")
    fig_dir = repo_path(config, "figures_dir")
    rank_heatmap(config, new_rank_maps(results, cfg["primary"]), fig_dir / "fig12_betweenness_rank_heatmap_greyscale.png",
                 "undirected, unweighted")
    rank_heatmap(config, new_rank_maps(results, "adjusted"), fig_dir / "fig12b_betweenness_adjusted_rank_heatmap_greyscale.png",
                 "frequency-adjusted")
    return {"results": results, "crosscheck": crosscheck}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    out = run(args.config)
    print(f"igraph vs networkx: max |diff| {out['crosscheck']['max_abs_diff']:.2e}")
    for t, r in out["results"].items():
        ranks = {w: rank_of(r, "undirected", w) for w in ("сказать", "говорить")}
        print(f"{t}: replication max diff {r['old_replication']['max_abs_diff']:.1e}, top-100 same "
              f"{r['old_replication']['top100_same']}; undirected ranks {ranks}")


if __name__ == "__main__":
    main()
