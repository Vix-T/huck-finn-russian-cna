"""Burrows-style Delta, clustering and embeddings from data/interim/tagged/Ru#.tsv.

Units are 5,000-word paragraph-aligned blocks and whole texts. Features are relative frequencies of the
most frequent lemmas (ranked by mean block relative frequency), z-scored with means and SDs taken over
all blocks. Distances: Cosine Delta (primary) and Classic Delta, over a grid of MFW sizes and culling
levels. Clustering: average linkage (complete as a check, Ward on L2-normalized vectors as a
sensitivity check), a majority-rule consensus tree and an Eder-style consensus network over the grid.
Block level: nearest-neighbour coherence, silhouette, and nearest other-translation neighbours.
Embeddings: PCA of blocks, classical MDS of texts, and (appendix) spectral embedding. The old Figures
7-8 and the old Delta are recomputed exactly from the old local folder (read-only).

Writes docs/delta/delta_report.md (tables only), data/interim/delta/ and greyscale figures in
docs/amendment/figures/.

Usage:
    uv run --frozen python -m huckfinn_cna.delta [--config PATH]
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from itertools import combinations
from pathlib import Path

import numpy as np

from huckfinn_cna.config import DEFAULT_CONFIG, load_config, repo_path
from huckfinn_cna.lexical import lemma_key, nonoverlapping_starts, paragraph_starts
from huckfinn_cna.tag import read_tagged


# --- Units and counts ----------------------------------------------------------------


def surface_words(config: dict, text_id: str) -> list[str]:
    """Surface word tokens, lowercased, with the network-node character map (ё -> е)."""
    char_map = config["networks"]["lemma_char_map"]
    return [lemma_key(r["token"], char_map) for r in read_tagged(repo_path(config, "tagged_dir") / f"{text_id}.tsv")
            if r["is_word"] == "1"]


def ngram_set(words: list[str], n: int) -> set[tuple[str, ...]]:
    return set(zip(*[words[i:] for i in range(n)]))


def ngram_overlap(config: dict) -> dict:
    """Per n and text pair: shared distinct n-grams and their share of each text's distinct n-grams."""
    texts = config["corpus"]["texts"]
    words = {t: surface_words(config, t) for t in texts}
    out = {}
    for n in config["delta"]["overlap_ngrams"]:
        grams = {t: ngram_set(w, n) for t, w in words.items()}
        for a, b in combinations(texts, 2):
            shared = len(grams[a] & grams[b])
            out[(n, a, b)] = (shared, shared / len(grams[a]), shared / len(grams[b]))
    return out


def load_tokens(config: dict, text_id: str) -> tuple[list[str], np.ndarray, np.ndarray]:
    """Word-token lemma keys, content flags and paragraph offsets (bounds) of one text."""
    char_map, content = config["networks"]["lemma_char_map"], set(config["tag"]["content_upos"])
    rows = read_tagged(repo_path(config, "tagged_dir") / f"{text_id}.tsv")
    n_par = int(rows[-1]["para_id"]) if rows else 0
    keys, flags, paras = [], [], []
    for r in rows:
        if r["is_word"] == "1":
            keys.append(lemma_key(r["lemma"], char_map))
            flags.append(r["upos"] in content)
            paras.append(int(r["para_id"]) - 1)
    bounds = np.searchsorted(np.asarray(paras, dtype=np.int64), np.arange(n_par + 1))
    return keys, np.asarray(flags, dtype=bool), bounds


def block_starts(bounds: np.ndarray, n_tokens: int, size: int) -> list[int]:
    """Consecutive paragraph-aligned blocks of `size` word tokens (lexical's non-overlapping rule)."""
    return nonoverlapping_starts(paragraph_starts(bounds), n_tokens, size)


def unit_counts(keys: list[str], flags: np.ndarray, start: int, end: int, view: str) -> Counter:
    if view == "all":
        return Counter(keys[start:end])
    return Counter(k for k, f in zip(keys[start:end], flags[start:end]) if f)


def build_units(config: dict) -> dict:
    """Per view: block and whole-text count tables, unit labels and denominators."""
    cfg = config["delta"]
    units = {v: {"blocks": [], "block_text": [], "block_start": [], "texts": [], "block_total": [], "text_total": []}
             for v in cfg["views"]}
    for t in config["corpus"]["texts"]:
        keys, flags, bounds = load_tokens(config, t)
        starts = block_starts(bounds, len(keys), cfg["block_words"])
        for v in cfg["views"]:
            u = units[v]
            for s in starts:
                u["blocks"].append(unit_counts(keys, flags, s, s + cfg["block_words"], v))
                u["block_text"].append(t)
                u["block_start"].append(s)
                u["block_total"].append(cfg["block_words"])
            u["texts"].append(unit_counts(keys, flags, 0, len(keys), v))
            u["text_total"].append(len(keys))
    return units


def relative_matrix(counts: list[Counter], totals: list[int], vocab: list[str]) -> np.ndarray:
    return np.array([[c[w] / n for w in vocab] for c, n in zip(counts, totals)], dtype=float)


def rank_mfw(block_counts: list[Counter], block_totals: list[int]) -> list[str]:
    """Words ranked by mean per-block relative frequency; ties alphabetical."""
    mean = Counter()
    for c, n in zip(block_counts, block_totals):
        for w, k in c.items():
            mean[w] += k / n
    return sorted(mean, key=lambda w: (-mean[w], w))


def cull(ranked: list[str], text_counts: list[Counter], culling: int) -> list[str]:
    """Culling 0: all words; 100: words present in every whole text."""
    if culling == 0:
        return ranked
    if culling != 100:
        raise ValueError("only culling 0 and 100 are defined")
    return [w for w in ranked if all(c[w] > 0 for c in text_counts)]


def zscore(block_freq: np.ndarray, other: np.ndarray, ddof: int) -> tuple[np.ndarray, np.ndarray]:
    """z-scores of blocks and of `other` units with block means and SDs."""
    mean, sd = block_freq.mean(axis=0), block_freq.std(axis=0, ddof=ddof)
    if np.any(sd == 0):
        raise ValueError("a feature has zero SD across blocks")
    return (block_freq - mean) / sd, (other - mean) / sd


# --- Distances and clustering ---------------------------------------------------------


def cosine_delta(z: np.ndarray) -> np.ndarray:
    norm = z / np.linalg.norm(z, axis=1, keepdims=True)
    d = 1 - norm @ norm.T
    np.fill_diagonal(d, 0)
    return np.clip((d + d.T) / 2, 0, None)


def classic_delta(z: np.ndarray) -> np.ndarray:
    return np.abs(z[:, None, :] - z[None, :, :]).mean(axis=2)


DISTANCES = {"cosine": cosine_delta, "classic": classic_delta}


def l2_euclidean(z: np.ndarray) -> np.ndarray:
    """Euclidean distance between L2-normalized vectors (the only distance Ward is used on)."""
    norm = z / np.linalg.norm(z, axis=1, keepdims=True)
    return np.sqrt(np.clip(((norm[:, None, :] - norm[None, :, :]) ** 2).sum(axis=2), 0, None))


def linkage_tree(d: np.ndarray, method: str) -> np.ndarray:
    from scipy.cluster.hierarchy import linkage
    from scipy.spatial.distance import squareform
    return linkage(squareform(d, checks=False), method=method)


def clades(link: np.ndarray, n: int) -> set[frozenset[int]]:
    """Non-trivial clades (2 .. n-1 leaves) of a linkage matrix."""
    members = {i: frozenset([i]) for i in range(n)}
    out = set()
    for k, (a, b, _, _) in enumerate(link):
        members[n + k] = members[int(a)] | members[int(b)]
        if 1 < len(members[n + k]) < n:
            out.add(members[n + k])
    return out


def compatible(a: frozenset, b: frozenset) -> bool:
    return a <= b or b <= a or not (a & b)


def majority_consensus(trees: list[set[frozenset[int]]], threshold: float) -> tuple[list[tuple[frozenset, float]], list]:
    """Clades in >= threshold of the trees, with their support; conflicting clades at the threshold are dropped
    and returned separately."""
    counts = Counter(c for t in trees for c in t)
    kept = [(c, k / len(trees)) for c, k in counts.items() if k / len(trees) >= threshold]
    dropped = [(c, s) for c, s in kept if any(not compatible(c, o) for o, _ in kept if o != c)]
    kept = [(c, s) for c, s in kept if (c, s) not in dropped]
    return sorted(kept, key=lambda cs: (len(cs[0]), sorted(cs[0]))), dropped


def eder_edges(d: np.ndarray, weights: list[float]) -> Counter:
    """Undirected edge weights: each unit's nearest neighbours get weights[0], weights[1], ... in order."""
    out = Counter()
    for i in range(len(d)):
        order = [j for j in np.argsort(d[i], kind="stable") if j != i]
        for w, j in zip(weights, order):
            out[tuple(sorted((i, int(j))))] += w
    return out


# --- Block level --------------------------------------------------------------------------


def nearest_neighbours(d: np.ndarray) -> np.ndarray:
    dd = d.copy()
    np.fill_diagonal(dd, np.inf)
    return np.argmin(dd, axis=1)


def coherence(d: np.ndarray, labels: list[str]) -> float:
    nn = nearest_neighbours(d)
    return float(np.mean([labels[i] == labels[j] for i, j in enumerate(nn)]))


def silhouette(d: np.ndarray, labels: list[str]) -> float:
    from sklearn.metrics import silhouette_score
    return float(silhouette_score(d, labels, metric="precomputed"))


def other_translation_neighbours(d: np.ndarray, labels: list[str], texts: list[str]) -> np.ndarray:
    """Matrix [row text, column text]: blocks of the row text whose nearest block from another translation
    belongs to the column text."""
    lab = np.array(labels)
    out = np.zeros((len(texts), len(texts)), dtype=int)
    for i in range(len(d)):
        row = d[i].copy()
        row[lab == lab[i]] = np.inf
        out[texts.index(lab[i]), texts.index(lab[int(np.argmin(row))])] += 1
    return out


# --- Embeddings ----------------------------------------------------------------------------


def eta_squared(scores: np.ndarray, groups: list[str]) -> float:
    """Share of the variance of `scores` explained by group means."""
    g = np.array(groups)
    total = ((scores - scores.mean()) ** 2).sum()
    between = sum((g == k).sum() * (scores[g == k].mean() - scores.mean()) ** 2 for k in set(groups))
    return float(between / total) if total else 0.0


def pca(z: np.ndarray, k: int):
    from sklearn.decomposition import PCA
    model = PCA(n_components=k, svd_solver="full")
    return model, model.fit_transform(z)


def classical_mds(d: np.ndarray, k: int = 2) -> tuple[np.ndarray, np.ndarray]:
    """Torgerson MDS: coordinates and all eigenvalues of B = -1/2 J D² J (descending)."""
    n = len(d)
    j = np.eye(n) - np.ones((n, n)) / n
    b = -0.5 * j @ (d ** 2) @ j
    vals, vecs = np.linalg.eigh(b)
    order = np.argsort(vals)[::-1]
    vals, vecs = vals[order], vecs[:, order]
    coords = vecs[:, :k] * np.sqrt(np.clip(vals[:k], 0, None))
    return coords, vals


def cosine_affinity(z: np.ndarray) -> np.ndarray:
    norm = z / np.linalg.norm(z, axis=1, keepdims=True)
    return np.clip(norm @ norm.T, 0, None)


def normalized_laplacian_eigenvalues(affinity: np.ndarray) -> np.ndarray:
    """Eigenvalues (ascending) of the symmetric normalized Laplacian I - D^-1/2 W D^-1/2 (diagonal of W ignored,
    as scipy.sparse.csgraph.laplacian does)."""
    from scipy.sparse.csgraph import laplacian
    return np.sort(np.linalg.eigvalsh(laplacian(affinity, normed=True)))


def spectral(affinity: np.ndarray, random_state: int) -> np.ndarray:
    """sklearn spectral embedding. Its 'graph is not fully connected' warning is silenced here because the
    number of connected components is reported (see components)."""
    import warnings
    from sklearn.manifold import spectral_embedding
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Graph is not fully connected")
        return spectral_embedding(affinity, n_components=2, norm_laplacian=True, drop_first=True,
                                  random_state=random_state)


def components(affinity: np.ndarray) -> int:
    """Connected components of the graph with an edge wherever the off-diagonal affinity is > 0."""
    from scipy.sparse.csgraph import connected_components
    a = affinity.copy()
    np.fill_diagonal(a, 0)
    return int(connected_components(a > 0, directed=False)[0])


def relative_positions(labels: list[str]) -> np.ndarray:
    """Each block's centre as a share of its text's blocks (story position, 0-1)."""
    pos = np.empty(len(labels))
    for t in set(labels):
        idx = [i for i, x in enumerate(labels) if x == t]
        pos[idx] = (np.arange(len(idx)) + 0.5) / len(idx)
    return pos


def position_gap(d: np.ndarray, labels: list[str]) -> tuple[int, float, float]:
    """For blocks whose nearest block is from another translation: their number, the mean |story position
    difference| to that neighbour, and the mean difference to all other-translation blocks (expectation if
    position did not matter)."""
    pos, lab = relative_positions(labels), np.array(labels)
    nn = nearest_neighbours(d)
    cross = [i for i, j in enumerate(nn) if lab[i] != lab[j]]
    if not cross:
        return 0, float("nan"), float("nan")
    gap = float(np.mean([abs(pos[i] - pos[nn[i]]) for i in cross]))
    base = float(np.mean([np.abs(pos[i] - pos[lab != lab[i]]).mean() for i in cross]))
    return len(cross), gap, base


def same_layout(a: np.ndarray, b: np.ndarray, tol: float = 1e-6) -> bool:
    """Equal up to the sign of each component."""
    return all(np.allclose(a[:, k], b[:, k], atol=tol) or np.allclose(a[:, k], -b[:, k], atol=tol) for k in range(a.shape[1]))


# --- Grid ----------------------------------------------------------------------------------


def setting_key(view: str, n: int, culling: int, distance: str) -> str:
    return f"{view}|{n}|{culling}|{distance}"


def run_grid(config: dict, units: dict) -> dict:
    """Distance matrices for blocks and whole texts at every setting, plus the z-scored features used."""
    cfg = config["delta"]
    out = {"settings": {}, "features": {}, "mfw_available": {}}
    for v in cfg["views"]:
        u = units[v]
        ranked = rank_mfw(u["blocks"], u["block_total"])
        for c in cfg["culling"]:
            words_all = cull(ranked, u["texts"], c)
            out["mfw_available"][f"{v}|{c}"] = len(words_all)
            for n in cfg["mfw"]:
                words = words_all[:n]
                zb, zt = zscore(relative_matrix(u["blocks"], u["block_total"], words),
                                relative_matrix(u["texts"], u["text_total"], words), cfg["zscore_ddof"])
                out["features"][f"{v}|{n}|{c}"] = {"words": words, "blocks": zb, "texts": zt}
                for dist in cfg["distances"]:
                    out["settings"][setting_key(v, n, c, dist)] = {
                        "blocks": DISTANCES[dist](zb), "texts": DISTANCES[dist](zt), "n_words": len(words)}
    return out


# --- Old method ----------------------------------------------------------------------------


def old_path(config: dict, name: str) -> Path:
    return Path(config["paths"]["old_repo"]) / name


def read_old_dtm(path: Path) -> tuple[list[str], list[str], np.ndarray]:
    with path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    return [r[0] for r in rows[1:]], rows[0][1:], np.array([[float(x) for x in r[1:]] for r in rows[1:]])


def old_square_linkage_clades(square: np.ndarray) -> set[frozenset[int]]:
    """The old cosine dendrogram: linkage given a square matrix, which scipy treats as observation vectors."""
    import warnings
    from scipy.cluster.hierarchy import ClusterWarning, linkage
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ClusterWarning)      # the misuse is reproduced on purpose
        return clades(linkage(square, method="ward"), len(square))


def old_results(config: dict) -> dict:
    """The old Figure 7 (cosine similarity of raw counts, stop words kept), the old Figure 8 (TF-IDF spectral
    embedding), the old Classic Delta (z over 5 documents, ddof 0, full vocabulary) with Ward, and the old cosine
    dendrogram (linkage given the square matrix), recomputed from the old local folder."""
    from scipy.cluster.hierarchy import linkage
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_distances, cosine_similarity
    cfg = config["delta"]
    out = {}
    for view, name in (("all", cfg["old_dtm_all"]), ("content", cfg["old_dtm_content"])):
        labels, vocab, x = read_old_dtm(old_path(config, name))
        z = (x - x.mean(axis=0)) / np.where(x.std(axis=0) == 0, 1, x.std(axis=0))   # StandardScaler (ddof 0)
        n = len(x)
        delta = np.array([[np.mean(np.abs(z[i] - z[j])) for j in range(n)] for i in range(n)])
        cos_d = cosine_distances(x)
        out[view] = {
            "labels": labels, "vocab_size": len(vocab), "similarity": cosine_similarity(x),
            "classic_delta": delta,
            "delta_ward_clades": clades(linkage(delta[np.triu_indices(n, 1)], method="ward"), n),
            "cosine_square_ward_clades": old_square_linkage_clades(cos_d),
            "cosine_condensed_average_clades": clades(linkage(cos_d[np.triu_indices(n, 1)], method="average"), n),
        }
    corpus = [old_path(config, cfg["old_lemmatized"].format(i=i)).read_text(encoding="utf-8") for i in range(1, 6)]
    vectorizer = TfidfVectorizer()
    tfidf = vectorizer.fit_transform(corpus)
    affinity = cosine_similarity(tfidf)
    tokens = [doc.split() for doc in corpus]
    dropped = Counter(w for doc in tokens for w in doc if len(w) < 2)
    out["spectral"] = {
        "embedding": spectral(affinity, cfg["old_tfidf_random_state"]),
        "same_across_states": all(same_layout(spectral(affinity, cfg["old_tfidf_random_state"]), spectral(affinity, s), tol=1e-4)
                                  for s in cfg["spectral_check_states"]),
        "layouts": {s: spectral(affinity, s) for s in cfg["spectral_check_states"]},
        "eigenvalues": normalized_laplacian_eigenvalues(affinity),
        "affinity": affinity,
        "dropped_share": [sum(1 for w in doc if len(w) < 2) / len(doc) for doc in tokens],
        "dropped_top": dropped.most_common(8),
    }
    return out


# --- Analysis ------------------------------------------------------------------------------


def analyse(config: dict) -> dict:
    cfg, texts = config["delta"], config["corpus"]["texts"]
    units = build_units(config)
    grid = run_grid(config, units)
    pv, pr = cfg["primary_view"], cfg["primary"]
    res = {"units": units, "grid": grid, "texts": texts}

    # Text-level trees over the grid (primary view; both distances) and the consensus.
    trees, edges, per_setting = [], Counter(), {}
    for n in cfg["mfw"]:
        for c in cfg["culling"]:
            for dist in cfg["distances"]:
                d = grid["settings"][setting_key(pv, n, c, dist)]["texts"]
                cl = clades(linkage_tree(d, cfg["linkage"]), len(texts))
                trees.append(cl)
                e = eder_edges(d, cfg["eder_weights"])
                edges.update(e)
                per_setting[(n, c, dist)] = {"clades": cl, "nearest": nearest_neighbours(d).tolist(),
                                              "complete": clades(linkage_tree(d, cfg["linkage_check"]), len(texts))}
    res["consensus"], res["consensus_dropped"] = majority_consensus(trees, cfg["consensus_threshold"])
    res["clade_support"] = Counter(c for t in trees for c in t)
    res["n_trees"] = len(trees)
    res["eder"] = edges
    res["per_setting"] = per_setting

    # Primary setting in detail.
    key = setting_key(pv, pr["mfw"], pr["culling"], pr["distance"])
    d_text = grid["settings"][key]["texts"]
    feats = grid["features"][f"{pv}|{pr['mfw']}|{pr['culling']}"]
    res["primary"] = {
        "key": key, "texts": d_text,
        "average": linkage_tree(d_text, cfg["linkage"]),
        "complete": linkage_tree(d_text, cfg["linkage_check"]),
        "ward_l2": linkage_tree(l2_euclidean(feats["texts"]), "ward"),
    }

    # Block level, every setting; detail at the primary setting.
    labels = units[pv]["block_text"]
    res["block_grid"] = {}
    for k, s in grid["settings"].items():
        lab = units[k.split("|")[0]]["block_text"]
        n_cross, gap, base = position_gap(s["blocks"], lab)
        res["block_grid"][k] = {"coherence": coherence(s["blocks"], lab), "silhouette": silhouette(s["blocks"], lab),
                                "cross": n_cross, "gap": gap, "gap_base": base}
    d_blocks = grid["settings"][key]["blocks"]
    nn = nearest_neighbours(d_blocks)
    res["blocks"] = {
        "counts": Counter(labels),
        "per_text_coherence": {t: float(np.mean([labels[j] == t for i, j in enumerate(nn) if labels[i] == t])) for t in texts},
        "other": other_translation_neighbours(d_blocks, labels, texts),
    }

    # PCA on standardized block relative frequencies.
    res["pca"] = {}
    groups_ab = ["abridged" if t in cfg["abridged"] else "full" for t in labels]
    for n in cfg["pca_mfw"]:
        f = grid["features"][f"{pv}|{n}|0"]
        model, scores = pca(f["blocks"], cfg["pca_components"])
        comps = []
        for k in range(cfg["pca_components"]):
            load = model.components_[k]
            order = np.argsort(load)
            comps.append({
                "explained": float(model.explained_variance_ratio_[k]),
                "positive": [(f["words"][i], float(load[i])) for i in order[::-1][:cfg["pca_top_loadings"]]],
                "negative": [(f["words"][i], float(load[i])) for i in order[:cfg["pca_top_loadings"]]],
                "eta2_translation": eta_squared(scores[:, k], labels),
                "eta2_abridged": eta_squared(scores[:, k], groups_ab),
                "text_means": {t: float(scores[np.array(labels) == t, k].mean()) for t in texts},
            })
            comps[-1]["flag_abridged"] = comps[-1]["eta2_abridged"] >= cfg["abridged_flag_eta2"]
        res["pca"][n] = comps

    # Classical MDS.
    ms = cfg["mds_setting"]
    d_mds = grid["settings"][setting_key(pv, ms["mfw"], ms["culling"], "cosine")]["texts"]
    res["mds"] = dict(zip(("coords", "eigenvalues"), classical_mds(d_mds)))

    # Spectral embedding (appendix).
    aff = cosine_affinity(feats["texts"])
    emb = spectral(aff, cfg["spectral_random_state"])
    res["spectral"] = {
        "affinity": aff, "embedding": emb, "eigenvalues": normalized_laplacian_eigenvalues(aff),
        "zero_affinities": int(((aff == 0) & ~np.eye(len(texts), dtype=bool)).sum() // 2),
        "components": components(aff),
        "same_across_states": all(same_layout(emb, spectral(aff, s)) for s in cfg["spectral_check_states"]),
    }
    grid_eigs = {}
    for n in cfg["mfw"]:
        for c in cfg["culling"]:
            a = cosine_affinity(grid["features"][f"{pv}|{n}|{c}"]["texts"])
            e = spectral(a, cfg["spectral_random_state"])
            grid_eigs[(n, c)] = {"eigenvalues": normalized_laplacian_eigenvalues(a),
                                 "nearest": nearest_neighbours(np.linalg.norm(e[:, None] - e[None], axis=2)).tolist(),
                                 "zero_affinities": int(((a == 0) & ~np.eye(len(texts), dtype=bool)).sum() // 2),
                                 "components": components(a)}
    res["spectral"]["grid"] = grid_eigs

    # Appendix Figure 13: top content words, ranked within the set per text.
    content_totals = Counter()
    for c in units["content"]["texts"]:
        content_totals.update(c)
    top = [w for w, _ in sorted(content_totals.items(), key=lambda kv: (-kv[1], kv[0]))[:cfg["fig13_top_n"]]]
    ranks = {}
    for t, c in zip(texts, units["content"]["texts"]):
        vals = np.array([c[w] for w in top], dtype=float)
        ranks[t] = [int(1 + (vals > v).sum()) for v in vals]
    res["fig13"] = {"words": top, "ranks": ranks, "counts": {t: [c[w] for w in top] for t, c in zip(texts, units["content"]["texts"])}}

    res["overlap"] = ngram_overlap(config)
    res["old"] = old_results(config)
    return res


# --- Report ---------------------------------------------------------------------------------


def clade_label(c: frozenset, texts: list[str]) -> str:
    return "(" + ", ".join(texts[i] for i in sorted(c)) + ")"


def tree_string(cl: set[frozenset[int]], texts: list[str]) -> str:
    """Nested-parenthesis string of a set of compatible clades."""
    def build(members: frozenset) -> str:
        children, rest = [], set(members)
        for c in sorted((c for c in cl if c < members), key=len, reverse=True):
            if c <= rest and not any(c < o < members for o in cl):
                children.append(build(c))
                rest -= c
        parts = children + [texts[i] for i in sorted(rest)]
        return "(" + ", ".join(parts) + ")"
    return build(frozenset(range(len(texts))))


def fmt_matrix(d: np.ndarray, texts: list[str], digits: int = 4) -> list[str]:
    L = ["| | " + " | ".join(texts) + " |", "|---|" + "---|" * len(texts)]
    for i, t in enumerate(texts):
        L.append(f"| {t} | " + " | ".join(f"{d[i, j]:.{digits}f}" for j in range(len(texts))) + " |")
    return L


def nearest_of(d: np.ndarray, texts: list[str]) -> dict[str, str]:
    return {texts[i]: texts[j] for i, j in enumerate(nearest_neighbours(d))}


def render_report(config: dict, res: dict) -> str:
    cfg, texts = config["delta"], res["texts"]
    units, grid = res["units"], res["grid"]
    pv, pr = cfg["primary_view"], cfg["primary"]
    L = ["# Delta, clustering and embedding", "",
         "Generated by `uv run --frozen python -m huckfinn_cna.delta` from `data/interim/tagged/Ru#.tsv`. Tables only.",
         f"Tokens: word tokens, Stanza lemmas lowercased, ё → е. Views: **all** (primary) and **content** "
         f"({', '.join(config['tag']['content_upos'])}). Relative frequency = count / word tokens in the unit.",
         f"MFW ranked by mean block relative frequency; grid N = {cfg['mfw'][0]}–{cfg['mfw'][-1]} step {cfg['mfw'][1] - cfg['mfw'][0]}, "
         f"culling {cfg['culling']} %; z-scores with block means and SDs (ddof {cfg['zscore_ddof']}). "
         f"Primary setting: view {pv}, N = {pr['mfw']}, culling {pr['culling']} %, {pr['distance']} Delta, {cfg['linkage']} linkage.", ""]

    L += ["## Units", "", f"Blocks of {cfg['block_words']:,} word tokens, paragraph-aligned, remainder dropped.", "",
          "| Text | Word tokens | Blocks | Words in blocks | Words dropped |", "|---|---|---|---|---|"]
    for i, t in enumerate(texts):
        nb = units[pv]["block_text"].count(t)
        tot = units[pv]["text_total"][i]
        L.append(f"| {t} | {tot:,} | {nb} | {nb * cfg['block_words']:,} | {tot - nb * cfg['block_words']:,} |")
    L += ["", "Words available after culling: " + "; ".join(f"{k}: {v:,}" for k, v in grid["mfw_available"].items()) + "."]

    # Block-level first.
    L += ["", "## Do blocks group by translation?", "",
          "Coherence = share of blocks whose nearest block (same distance) is from the same translation. Silhouette uses "
          "translation as the label (range −1 to 1).", "",
          "| View | Culling | Distance | " + " | ".join(f"N={n}" for n in cfg["mfw"]) + " |", "|---|---|---|" + "---|" * len(cfg["mfw"])]
    for v in cfg["views"]:
        for c in cfg["culling"]:
            for dist in cfg["distances"]:
                cells = [res["block_grid"][setting_key(v, n, c, dist)] for n in cfg["mfw"]]
                L.append(f"| {v} | {c} | {dist} | " + " | ".join(f"{x['coherence']:.2f} / {x['silhouette']:.2f}" for x in cells) + " |")
    L += ["", "Story position: for blocks whose nearest block is from another translation, the mean |difference in story "
          "position| (block centre as a share of its text, 0–1) to that neighbour, against the mean difference to all "
          "other-translation blocks (what position alone would give if it did not matter). Cosine Delta, culling 0 %.", "",
          "| View | N | Blocks with other-translation nearest neighbour | Mean position gap | Expected if position did not matter |",
          "|---|---|---|---|---|"]
    for v in cfg["views"]:
        for n in cfg["mfw"]:
            x = res["block_grid"][setting_key(v, n, 0, "cosine")]
            L.append(f"| {v} | {n} | {x['cross']} of {len(units[v]['block_text'])} | {x['gap']:.3f} | {x['gap_base']:.3f} |")
    b = res["blocks"]
    L += ["", f"Primary setting, per translation: coherence " +
          ", ".join(f"{t} {b['per_text_coherence'][t]:.2f} ({b['counts'][t]} blocks)" for t in texts) + ".", "",
          "Nearest block from another translation (rows: the block's translation; counts of blocks):", "",
          "| | " + " | ".join(texts) + " |", "|---|" + "---|" * len(texts)]
    for i, t in enumerate(texts):
        L.append(f"| {t} | " + " | ".join(str(b["other"][i, j]) for j in range(len(texts))) + " |")

    L += ["", "## Shared wording between translations", "",
          "Distinct word n-grams (surface forms, lowercased, ё → е) found in both texts, and their share of each text's "
          "distinct n-grams.", "", "| Pair | " + " | ".join(f"{n}-grams shared | share of first | share of second"
                                                            for n in cfg["overlap_ngrams"]) + " |",
          "|---|" + "---|---|---|" * len(cfg["overlap_ngrams"])]
    for a, b in combinations(texts, 2):
        L.append(f"| {a}–{b} | " + " | ".join(
            f"{res['overlap'][(n, a, b)][0]:,} | {res['overlap'][(n, a, b)][1]:.1%} | {res['overlap'][(n, a, b)][2]:.1%}"
            for n in cfg["overlap_ngrams"]) + " |")

    # Text-level.
    L += ["", f"## Text-level distances, primary setting ({pr['distance']} Delta, N = {pr['mfw']}, culling {pr['culling']} %)", ""]
    L += fmt_matrix(res["primary"]["texts"], texts)
    p = res["primary"]
    L += ["", "| Linkage | Tree |", "|---|---|",
          f"| average (primary) | {tree_string(clades(p['average'], 5), texts)} |",
          f"| complete (check) | {tree_string(clades(p['complete'], 5), texts)} |",
          f"| Ward on L2-normalized Euclidean (sensitivity) | {tree_string(clades(p['ward_l2'], 5), texts)} |"]

    L += ["", f"## Consensus over the grid ({res['n_trees']} trees: {len(cfg['mfw'])} N × {len(cfg['culling'])} culling × "
          f"{len(cfg['distances'])} distances, view {pv}, {cfg['linkage']} linkage)", "",
          f"Majority-rule consensus (clades in ≥ {cfg['consensus_threshold']:.0%} of trees): "
          f"**{tree_string({c for c, _ in res['consensus']}, texts)}**", "",
          "| Clade | Support | " + " | ".join(f"{d} Delta" for d in cfg["distances"]) + " |",
          "|---|---|" + "---|" * len(cfg["distances"])]
    for c, k in sorted(res["clade_support"].items(), key=lambda kv: -kv[1]):
        by = [sum(c in s["clades"] for (n, cu, d), s in res["per_setting"].items() if d == dist) for dist in cfg["distances"]]
        per = res["n_trees"] // len(cfg["distances"])
        L.append(f"| {clade_label(c, texts)} | {k / res['n_trees']:.0%} ({k}/{res['n_trees']}) | "
                 + " | ".join(f"{x}/{per}" for x in by) + " |")
    if res["consensus_dropped"]:
        L.append("\nConflicting clades at the threshold, dropped: " + ", ".join(clade_label(c, texts) for c, _ in res["consensus_dropped"]))
    L += ["", f"Eder-style consensus network (weights {cfg['eder_weights']} for the nearest and next two neighbours, summed over "
          f"the {res['n_trees']} settings; maximum possible per edge {2 * cfg['eder_weights'][0] * res['n_trees']:.0f}):", "",
          "| Edge | Weight |", "|---|---|"]
    for (i, j), w in sorted(res["eder"].items(), key=lambda kv: -kv[1]):
        L.append(f"| {texts[i]} – {texts[j]} | {w:.1f} |")
    near = Counter()
    for s in res["per_setting"].values():
        for i, j in enumerate(s["nearest"]):
            near[(texts[i], texts[j])] += 1
    L += ["", "Nearest text over the grid (share of settings):", "", "| Text | " + " | ".join(texts) + " |", "|---|" + "---|" * len(texts)]
    for t in texts:
        L.append(f"| {t} | " + " | ".join("—" if u == t else f"{near[(t, u)] / res['n_trees']:.0%}" for u in texts) + " |")

    # PCA.
    L += ["", "## PCA of blocks (standardized relative frequencies, view all, culling 0 %)", ""]
    for n, comps in res["pca"].items():
        L += [f"### N = {n}", "", "| PC | Explained variance | η² translation | η² abridged vs full | Flag | Mean score by text | "
              "Top + loadings | Top − loadings |", "|---|---|---|---|---|---|---|---|"]
        for k, c in enumerate(comps, start=1):
            means = ", ".join(f"{t} {c['text_means'][t]:+.1f}" for t in texts)
            L.append(f"| PC{k} | {c['explained']:.1%} | {c['eta2_translation']:.2f} | {c['eta2_abridged']:.2f} | "
                     f"{'abridged vs full' if c['flag_abridged'] else ''} | {means} | "
                     + ", ".join(w for w, _ in c["positive"]) + " | " + ", ".join(w for w, _ in c["negative"]) + " |")
        L.append("")

    # MDS.
    ev = res["mds"]["eigenvalues"]
    pos = ev[ev > 0].sum()
    L += [f"## Classical MDS (Cosine Delta, N = {cfg['mds_setting']['mfw']}, culling {cfg['mds_setting']['culling']} %)", "",
          "Eigenvalues of −½ J D² J: " + ", ".join(f"{x:.4f}" for x in ev) +
          f". Dimensions 1–2 carry {ev[:2].sum() / pos:.1%} of the positive eigenvalue sum.", "",
          "| Text | Dim 1 | Dim 2 |", "|---|---|---|"]
    for t, (x, y) in zip(texts, res["mds"]["coords"]):
        L.append(f"| {t} | {x:+.4f} | {y:+.4f} |")

    # Spectral.
    sp = res["spectral"]
    L += ["", "## Spectral embedding (appendix)", "",
          "Text-level z-scores at the primary setting; affinity = cosine similarity with negatives set to 0; symmetric "
          "normalized Laplacian L = I − D^−1/2 W D^−1/2; the first eigenvector is dropped and the next two, rescaled by "
          f"D^−1/2, are the coordinates (sklearn `spectral_embedding`, `norm_laplacian=True`, random_state {cfg['spectral_random_state']}).", "",
          "Eigenvalues (ascending): " + ", ".join(f"{x:.4f}" for x in sp["eigenvalues"]) +
          f". Zero affinities (pairs with negative cosine similarity): {sp['zero_affinities']} of 10; connected components: "
          f"{sp['components']}. "
          f"Same layout (up to sign) for random_state {cfg['spectral_check_states']}: {'yes' if sp['same_across_states'] else 'no'}.", "",
          "Affinity matrix:", ""] + fmt_matrix(sp["affinity"], texts, 3) + ["", "| Text | Component 1 | Component 2 |", "|---|---|---|"]
    for t, (x, y) in zip(texts, sp["embedding"]):
        L.append(f"| {t} | {x:+.4f} | {y:+.4f} |")
    L += ["", "Across the grid (view all): eigenvalues λ2, λ3, λ4, zero affinities, and each text's nearest text in the 2-D layout.", "",
          "| N | Culling | λ2 | λ3 | λ4 | λ3 − λ2 | Zero affinities | Components | Nearest in layout |",
          "|---|---|---|---|---|---|---|---|---|"]
    for (n, c), g in sp["grid"].items():
        e = g["eigenvalues"]
        L.append(f"| {n} | {c} | {e[1]:.4f} | {e[2]:.4f} | {e[3]:.4f} | {e[2] - e[1]:.4f} | {g['zero_affinities']} | {g['components']} | "
                 + ", ".join(f"{texts[i]}→{texts[j]}" for i, j in enumerate(g["nearest"])) + " |")

    # Old vs new.
    o = res["old"]
    L += ["", "## Old versus new", "",
          "Old values recomputed from the old local folder (read-only): Figure 7 from `document_term_matrix_with_stops.csv` "
          "(cosine similarity of raw counts over the full vocabulary); Figure 8 from `Ru#_clean_lemmatized.txt` (TF-IDF, "
          f"random_state {cfg['old_tfidf_random_state']}); old Classic Delta from the DTMs with z-scores over the 5 documents (ddof 0).", "",
          f"### Old Figure 7: cosine similarity, all words ({o['all']['vocab_size']:,} words)", ""] + fmt_matrix(o["all"]["similarity"], texts)
    for view in ("all", "content"):
        ov = o[view]
        L += ["", f"### Old Classic Delta, {view} words ({ov['vocab_size']:,} words, full vocabulary)", ""] + fmt_matrix(ov["classic_delta"], texts)
        L += ["", "| Old clustering | Tree |", "|---|---|",
              f"| Classic Delta, Ward on condensed distances | {tree_string(ov['delta_ward_clades'], texts)} |",
              f"| Cosine, Ward given the square matrix (rows clustered as vectors) | {tree_string(ov['cosine_square_ward_clades'], texts)} |",
              f"| Cosine, average linkage on condensed distances (correct use) | {tree_string(ov['cosine_condensed_average_clades'], texts)} |"]
    sp_old = o["spectral"]
    L += ["", "### Old Figure 8: spectral embedding (TF-IDF)", "",
          "Normalized Laplacian eigenvalues: " + ", ".join(f"{x:.4f}" for x in sp_old["eigenvalues"])
          + f"; connected components {components(sp_old['affinity'])}; smallest off-diagonal affinity "
          + f"{sp_old['affinity'][~np.eye(5, dtype=bool)].min():.4f}. Same layout (up to sign, tolerance 1e-4) for random_state "
          + f"{cfg['spectral_check_states']}: {'yes' if sp_old['same_across_states'] else 'no'}.",
          "Share of tokens dropped by TfidfVectorizer's default token pattern (one-letter words): "
          + ", ".join(f"{t} {s:.1%}" for t, s in zip(texts, sp_old["dropped_share"]))
          + "; most frequent: " + ", ".join(f"{w} ({k:,})" for w, k in sp_old["dropped_top"]) + ".", "",
          "| Text | Component 1 | Component 2 |", "|---|---|---|"]
    for t, (x, y) in zip(texts, sp_old["embedding"]):
        L.append(f"| {t} | {x:+.4f} | {y:+.4f} |")

    old_near = {}
    for i, t in enumerate(texts):
        row = o["all"]["similarity"][i].copy()
        row[i] = -np.inf
        old_near[t] = texts[int(np.argmax(row))]
    new_near = nearest_of(res["primary"]["texts"], texts)
    L += ["", "### Nearest text: old Figure 7 vs new primary setting", "", "| Text | Old (Figure 7) | New (primary) | New: share of grid |",
          "|---|---|---|---|"]
    for t in texts:
        L.append(f"| {t} | {old_near[t]} | {new_near[t]} | {near[(t, new_near[t])] / res['n_trees']:.0%} |")

    L += ["", "## Appendix Figure 13: top content words", "",
          f"Top {cfg['fig13_top_n']} content lemmas by total count over the five texts; rank within that set per text (1 = most frequent).", "",
          "| Word | " + " | ".join(texts) + " |", "|---|" + "---|" * len(texts)]
    f13 = res["fig13"]
    for k, w in enumerate(f13["words"]):
        L.append(f"| {w} | " + " | ".join(f"{f13['ranks'][t][k]} ({f13['counts'][t][k]})" for t in texts) + " |")
    return "\n".join(L)


# --- Figures ---------------------------------------------------------------------------------


def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


def _labels(config: dict, texts: list[str]) -> list[str]:
    return [config["corpus"]["labels"][t] for t in texts]


def heatmap_with_dendrogram(config: dict, d: np.ndarray, link: np.ndarray, texts: list[str], title: str, path: Path) -> None:
    """Distance heatmap (old Figure 7 style: Greys, black grid, 4 decimals) next to the dendrogram."""
    from scipy.cluster.hierarchy import dendrogram
    plt = _plt()
    labels = _labels(config, texts)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(18, 7.5), gridspec_kw={"width_ratios": [1.15, 1]})
    off = d[~np.eye(len(d), dtype=bool)]
    im = ax1.imshow(d, cmap="Greys", vmin=0, vmax=off.max())
    for i in range(len(d)):
        for j in range(len(d)):
            ax1.text(j, i, f"{d[i, j]:.4f}", ha="center", va="center", fontsize=11,
                     color="white" if d[i, j] > off.max() * 0.6 else "black")
    ax1.set_xticks(range(len(d)), labels, rotation=90)
    ax1.set_yticks(range(len(d)), labels)
    ax1.set_xticks(np.arange(-.5, len(d)), minor=True)
    ax1.set_yticks(np.arange(-.5, len(d)), minor=True)
    ax1.grid(which="minor", color="black", linewidth=0.5)
    ax1.tick_params(which="both", length=0)
    fig.colorbar(im, ax=ax1, fraction=0.046, pad=0.04)
    ax1.set_title("Distance matrix", fontsize=14)
    dendrogram(link, labels=labels, orientation="right", leaf_font_size=12, link_color_func=lambda k: "black", ax=ax2)
    ax2.set_title("Average-linkage dendrogram", fontsize=14)
    ax2.set_xlabel("Cosine Delta", fontsize=12)
    for s in ("top", "right"):
        ax2.spines[s].set_visible(False)
    fig.suptitle(title, fontsize=16)
    fig.tight_layout()
    fig.savefig(path, dpi=config["pos_check"]["figure_dpi"], bbox_inches="tight")
    plt.close(fig)


def scatter(config: dict, xy: np.ndarray, texts: list[str], title: str, xlabel: str, ylabel: str, path: Path) -> None:
    """Old Figure 8 style scatter."""
    plt = _plt()
    fig, ax = plt.subplots(figsize=(10, 7))
    ax.scatter(xy[:, 0], xy[:, 1], s=150, color="black", alpha=0.75, edgecolor="grey")
    groups: dict[tuple[float, float], list[str]] = {}
    for (x, y), lab in zip(xy, _labels(config, texts)):        # coincident points share one stacked label
        groups.setdefault((round(float(x), 6), round(float(y), 6)), []).append(lab)
    for (x, y), labs in groups.items():
        ax.annotate("\n".join(labs), (x, y), textcoords="offset points", xytext=(7, 7), ha="left", va="bottom", fontsize=11)
    pad_x = (xy[:, 0].max() - xy[:, 0].min()) * 0.25 or 0.1
    ax.set_xlim(xy[:, 0].min() - pad_x * 0.3, xy[:, 0].max() + pad_x)
    ax.set_title(title, fontsize=16)
    ax.set_xlabel(xlabel, fontsize=12)
    ax.set_ylabel(ylabel, fontsize=12)
    fig.tight_layout()
    fig.savefig(path, dpi=config["pos_check"]["figure_dpi"], bbox_inches="tight")
    plt.close(fig)


def consensus_tree_figure(config: dict, consensus: list, support: Counter, n_trees: int, texts: list[str], path: Path) -> None:
    """Rectangular cladogram of the majority-rule consensus (multifurcations allowed), clade support labelled."""
    plt = _plt()
    labels = _labels(config, texts)
    clade_set = {c for c, _ in consensus}
    full = frozenset(range(len(texts)))

    def children(members):
        kids, rest = [], set(members)
        for c in sorted((c for c in clade_set if c < members), key=len, reverse=True):
            if c <= rest and not any(c < o < members for o in clade_set):
                kids.append(c)
                rest -= c
        return kids + [frozenset([i]) for i in sorted(rest)]

    order, ypos = [], {}

    def leaves(members):
        if len(members) == 1:
            order.append(next(iter(members)))
            return
        for k in children(members):
            leaves(k)
    leaves(full)
    for y, i in enumerate(order):
        ypos[frozenset([i])] = y

    def depth(members):
        return 0 if len(members) == 1 else 1 + max(depth(k) for k in children(members))
    max_depth = depth(full)
    fig, ax = plt.subplots(figsize=(10, 6))

    def draw(members):
        """Returns (x, y) of the node; x = depth from the leaves."""
        if len(members) == 1:
            return 0.0, ypos[members]
        pts = [draw(k) for k in children(members)]
        x = float(depth(members))
        y = float(np.mean([p[1] for p in pts]))
        for px, py in pts:
            ax.plot([px, x], [py, py], color="black", linewidth=1.5)
        ax.plot([x, x], [min(p[1] for p in pts), max(p[1] for p in pts)], color="black", linewidth=1.5)
        if members != full:
            ax.text(x - 0.05, y + 0.12, f"{support[members] / n_trees:.0%}", ha="right", va="bottom", fontsize=11)
        return x, y
    draw(full)
    for i in order:
        ax.text(-0.08, ypos[frozenset([i])], labels[i], ha="left", va="center", fontsize=12)   # x axis is inverted
    ax.set_xlim(-2.6, max_depth + 0.3)
    ax.invert_xaxis()
    ax.axis("off")
    ax.set_title(f"Majority-rule consensus tree ({n_trees} average-linkage trees: MFW 100–1000, culling 0/100%, Cosine and Classic Delta)",
                 fontsize=13)
    fig.tight_layout()
    fig.savefig(path, dpi=config["pos_check"]["figure_dpi"], bbox_inches="tight")
    plt.close(fig)


def consensus_network_figure(config: dict, edges: Counter, n_settings: int, texts: list[str], path: Path) -> None:
    """Eder-style consensus network: nodes on a circle in corpus order, edge width and label = summed weight."""
    plt = _plt()
    angles = np.pi / 2 - 2 * np.pi * np.arange(len(texts)) / len(texts)
    coords = np.column_stack([np.cos(angles), np.sin(angles)])
    labels = _labels(config, texts)
    fig, ax = plt.subplots(figsize=(10, 7.5))
    wmax = max(edges.values())
    for (i, j), w in sorted(edges.items(), key=lambda kv: kv[1]):
        (x1, y1), (x2, y2) = coords[i], coords[j]
        ax.plot([x1, x2], [y1, y2], color=str(0.75 - 0.75 * w / wmax), linewidth=1 + 9 * w / wmax, zorder=1)
        ax.text((x1 + x2) / 2, (y1 + y2) / 2, f"{w:.0f}", fontsize=10, ha="center", va="center",
                bbox={"facecolor": "white", "edgecolor": "none", "pad": 1}, zorder=3)
    ax.scatter(coords[:, 0], coords[:, 1], s=300, color="black", zorder=2)
    for (x, y), lab in zip(coords, labels):
        ax.annotate(lab, (x, y), textcoords="offset points", xytext=(14 if x >= -0.01 else -14, 12),
                    ha="left" if x >= -0.01 else "right", fontsize=12)
    ax.set_title(f"Consensus network ({n_settings} settings; nearest neighbour 1, runners-up 0.5)", fontsize=15)
    ax.margins(0.25)
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(path, dpi=config["pos_check"]["figure_dpi"], bbox_inches="tight")
    plt.close(fig)


def fig13(config: dict, f13: dict, texts: list[str], path: Path) -> None:
    """Old Figure 13 style: ranks of the top content words per text."""
    plt = _plt()
    words = f13["words"]
    grid = np.array([f13["ranks"][t] for t in texts], dtype=float)
    fig, ax = plt.subplots(figsize=(20, 8))
    im = ax.imshow(grid, cmap="Greys", aspect="auto", vmin=1, vmax=len(words))
    for i in range(len(texts)):
        for j in range(len(words)):
            ax.text(j, i, f"{grid[i, j]:.0f}", ha="center", va="center", fontsize=11,
                    color="white" if grid[i, j] > len(words) * 0.6 else "black")
    ax.set_xticks(range(len(words)), words, rotation=45, ha="right", fontsize=11)
    ax.set_yticks(range(len(texts)), _labels(config, texts), fontsize=11)
    ax.set_xticks(np.arange(-.5, len(words)), minor=True)
    ax.set_yticks(np.arange(-.5, len(texts)), minor=True)
    ax.grid(which="minor", color="white", linewidth=0.5)
    ax.tick_params(which="both", length=0)
    cbar = fig.colorbar(im, ax=ax, fraction=0.03, pad=0.02)
    cbar.set_label("Frequency Rank (1 = Most Frequent)")
    ax.set_title("Relative Rank of Most Frequent Content Words", fontsize=16, pad=20)
    ax.set_ylabel("Translation", fontsize=12)
    ax.set_xlabel(f"Top {len(words)} Content Words Over All Translations (Stanza lemmas)", fontsize=12)
    fig.tight_layout()
    fig.savefig(path, dpi=config["pos_check"]["figure_dpi"], bbox_inches="tight")
    plt.close(fig)


def figures(config: dict, res: dict) -> None:
    cfg, texts = config["delta"], res["texts"]
    out = repo_path(config, "figures_dir")
    out.mkdir(parents=True, exist_ok=True)
    pr = cfg["primary"]
    heatmap_with_dendrogram(config, res["primary"]["texts"], res["primary"]["average"], texts,
                            f"Cosine Delta (all words, {pr['mfw']} MFW, culling {pr['culling']}%)",
                            out / "fig7_cosine_delta_heatmap_dendrogram_greyscale.png")
    ev = res["mds"]["eigenvalues"]
    pos = ev[ev > 0].sum()
    scatter(config, res["mds"]["coords"], texts, "Classical MDS of Cosine Delta (all words, 300 MFW)",
            f"Dimension 1 ({ev[0] / pos:.0%})", f"Dimension 2 ({ev[1] / pos:.0%})", out / "fig8_mds_cosine_delta_greyscale.png")
    consensus_tree_figure(config, res["consensus"], res["clade_support"], res["n_trees"], texts,
                          out / "delta_consensus_tree_greyscale.png")
    consensus_network_figure(config, res["eder"], res["n_trees"], texts,
                             out / "delta_consensus_network_greyscale.png")
    sp = res["spectral"]
    note = (f"\nDEGENERATE: affinity graph has {sp['components']} disconnected components; λ1 = λ2 = λ3 = 0, layout arbitrary"
            if sp["components"] > 1 else "")
    scatter(config, sp["embedding"], texts, "Spectral Embedding of Russian Translations (appendix)" + note,
            "Component 1", "Component 2", out / "appendix_spectral_embedding_greyscale.png")
    fig13(config, res["fig13"], texts, out / "fig13_mfw_content_rank_heatmap_greyscale.png")


# --- Run --------------------------------------------------------------------------------------


def write_results(config: dict, res: dict) -> None:
    out = repo_path(config, "delta_results")
    out.mkdir(parents=True, exist_ok=True)
    texts = res["texts"]
    with (out / "text_distances.tsv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(["view", "mfw", "culling", "distance", "text_a", "text_b", "value"])
        for k, s in res["grid"]["settings"].items():
            v, n, c, dist = k.split("|")
            for i, j in combinations(range(len(texts)), 2):
                w.writerow([v, n, c, dist, texts[i], texts[j], f"{s['texts'][i, j]:.10g}"])
    u = res["units"][config["delta"]["primary_view"]]
    with (out / "blocks.tsv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(["block", "text", "start_word_index"])
        for i, (t, s) in enumerate(zip(u["block_text"], u["block_start"])):
            w.writerow([i, t, s])
    (out / "mfw.json").write_text(json.dumps({k: f["words"] for k, f in res["grid"]["features"].items()},
                                             ensure_ascii=False, indent=0), encoding="utf-8")


def run(config_path: Path = DEFAULT_CONFIG) -> dict:
    config = load_config(config_path)
    res = analyse(config)
    write_results(config, res)
    report = repo_path(config, "delta_report")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(render_report(config, res) + "\n", encoding="utf-8")
    figures(config, res)
    return res


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    res = run(args.config)
    cfg = load_config(args.config)["delta"]
    pr = cfg["primary"]
    key = setting_key(cfg["primary_view"], pr["mfw"], pr["culling"], pr["distance"])
    print(f"blocks: {dict(res['blocks']['counts'])}; primary coherence {res['block_grid'][key]['coherence']:.2f}, "
          f"silhouette {res['block_grid'][key]['silhouette']:.2f}")
    print("consensus:", tree_string({c for c, _ in res["consensus"]}, res["texts"]))
    print("spectral eigenvalues:", np.round(res["spectral"]["eigenvalues"], 4))


if __name__ == "__main__":
    main()
