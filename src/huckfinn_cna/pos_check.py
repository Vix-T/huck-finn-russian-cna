"""POS re-check of the old Figure 6 claim, from data/interim/tagged/Ru#.tsv.

Word tokens only (is_word = 1), stop words kept. For each text and each tagger (Stanza, spaCy):
UPOS proportions, the top-N UPOS rank order, NOUN:VERB ratios, the VerbForm breakdown, and
Stanza–spaCy UPOS agreement. Writes docs/tagging/tagging_report.md (counts and proportions only,
no text excerpts) and a greyscale rank heatmap of the Stanza tags in the style of the old Figure 6.

Usage:
    uv run python -m huckfinn_cna.pos_check [--config PATH]
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from huckfinn_cna.clean import word_stats
from huckfinn_cna.config import DEFAULT_CONFIG, load_config, repo_path
from huckfinn_cna.tag import read_tagged

TAGGERS = {"Stanza": ("upos", "verbform"), "spaCy": ("spacy_upos", "spacy_verbform")}


# --- Measures -------------------------------------------------------------------


def word_rows(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r["is_word"] == "1"]


def upos_counts(rows: list[dict], column: str) -> Counter:
    return Counter(r[column] for r in rows)


def ranks(counts: Counter) -> dict[str, int]:
    """Competition ranks (1 = most frequent; ties share the lower rank) over all tags."""
    out = {}
    for tag, n in counts.items():
        out[tag] = 1 + sum(1 for m in counts.values() if m > n)
    return out


def top_order(counts: Counter, n: int) -> list[str]:
    """Top-n tags, most frequent first; ties broken alphabetically so the order is stable."""
    return [tag for tag, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:n]]


def ratio(a: int, b: int) -> float | None:
    return a / b if b else None


def noun_verb_ratios(counts: Counter) -> dict:
    return {"noun_verb": ratio(counts["NOUN"], counts["VERB"]),
            "noun_verb_aux": ratio(counts["NOUN"], counts["VERB"] + counts["AUX"])}


def verbform_counts(rows: list[dict], upos_col: str, vf_col: str, upos_set: set[str]) -> Counter:
    """VerbForm values of tokens whose tag is in upos_set ('' = no VerbForm)."""
    return Counter(r[vf_col] for r in rows if r[upos_col] in upos_set)


def agreement(rows: list[dict]) -> tuple[float, Counter]:
    """Share of tokens with the same UPOS from both taggers, and (Stanza, spaCy) disagreement pairs."""
    pairs = Counter((r["upos"], r["spacy_upos"]) for r in rows if r["upos"] != r["spacy_upos"])
    return (1 - sum(pairs.values()) / len(rows) if rows else 0.0), pairs


# --- Bootstrap ----------------------------------------------------------------------

# Per-paragraph count columns and the statistics computed from summed counts.
COUNT_COLUMNS = ["NOUN", "VERB", "AUX", "verbs", "Fin", "Part", "Conv"]
STATISTICS = {
    "noun_verb": ("NOUN : VERB", lambda c: c["NOUN"] / c["VERB"]),
    "noun_verb_aux": ("NOUN : (VERB + AUX)", lambda c: c["NOUN"] / (c["VERB"] + c["AUX"])),
    "fin": ("Finite share of verbs", lambda c: c["Fin"] / c["verbs"]),
    "part": ("Participle share of verbs", lambda c: c["Part"] / c["verbs"]),
    "conv": ("Verbal adverb (Conv) share of verbs", lambda c: c["Conv"] / c["verbs"]),
}


def paragraph_counts(rows: list[dict], n_paragraphs: int, upos_col: str, vf_col: str, verb_upos: set[str]):
    """Array (paragraphs × COUNT_COLUMNS) of counts; 'verbs' = tokens tagged in verb_upos."""
    import numpy as np
    counts = np.zeros((n_paragraphs, len(COUNT_COLUMNS)), dtype=np.int64)
    col = {name: i for i, name in enumerate(COUNT_COLUMNS)}
    for r in rows:
        i, upos = int(r["para_id"]) - 1, r[upos_col]
        if upos in ("NOUN", "VERB", "AUX"):
            counts[i, col[upos]] += 1
        if upos in verb_upos:
            counts[i, col["verbs"]] += 1
            if r[vf_col] in ("Fin", "Part", "Conv"):
                counts[i, col[r[vf_col]]] += 1
    return counts


def statistics(sums) -> dict[str, float]:
    """STATISTICS from summed counts (a vector in COUNT_COLUMNS order)."""
    c = dict(zip(COUNT_COLUMNS, sums))
    return {key: fn(c) for key, (_, fn) in STATISTICS.items()}


def bootstrap_indices(n_paragraphs: int, resamples: int, seed: int, text_index: int):
    """Resampled paragraph indices (resamples × n_paragraphs), reproducible per text."""
    import numpy as np
    rng = np.random.default_rng([seed, text_index])
    return rng.integers(0, n_paragraphs, size=(resamples, n_paragraphs))


def bootstrap_ci(counts, indices, level: float) -> dict[str, tuple[float, float]]:
    """Percentile interval of each statistic over the resampled paragraph sets."""
    import numpy as np
    sums = counts[indices].sum(axis=1)                      # resamples × COUNT_COLUMNS
    c = {name: sums[:, i].astype(float) for i, name in enumerate(COUNT_COLUMNS)}
    alpha = (1 - level) / 2
    return {key: tuple(np.quantile(fn(c), [alpha, 1 - alpha])) for key, (_, fn) in STATISTICS.items()}


def overlap(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return a[0] <= b[1] and b[0] <= a[1]


def heatmap_tags(per_text: dict[str, Counter], n: int) -> list[str]:
    """The n tags most frequent over all texts (summed counts), as in the old Figure 6."""
    total = Counter()
    for c in per_text.values():
        total.update(c)
    return top_order(total, n)


# --- Report -----------------------------------------------------------------------


def fmt(x: float | None, digits: int = 3) -> str:
    return "—" if x is None else f"{x:.{digits}f}"


def pct(n: int, d: int) -> str:
    return f"{100 * n / d:.1f}%" if d else "—"


def build(config: dict) -> dict:
    """All measures, keyed by text."""
    pc, texts = config["pos_check"], config["corpus"]["texts"]
    audit = {Path(t["file"]).stem: t for t in json.loads(repo_path(config, "audit_json").read_text(encoding="utf-8"))["texts"]}
    tagged_dir, clean_dir = repo_path(config, "tagged_dir"), repo_path(config, "clean_dir")
    data = {}
    for text_index, text_id in enumerate(texts):
        rows = read_tagged(tagged_dir / f"{text_id}.tsv")
        meta = json.loads((tagged_dir / f"{text_id}.meta.json").read_text(encoding="utf-8"))
        words = word_rows(rows)
        entry = {"meta": meta, "audit": audit[text_id]["lengths"], "n_words": len(words),
                 "clean_words": word_stats((clean_dir / f"{text_id}.txt").read_text(encoding="utf-8"))["tokens"]}
        indices = bootstrap_indices(meta["paragraphs"], pc["bootstrap_resamples"], pc["bootstrap_seed"], text_index)
        for tagger, (upos_col, vf_col) in TAGGERS.items():
            counts = upos_counts(words, upos_col)
            para = paragraph_counts(words, meta["paragraphs"], upos_col, vf_col, set(pc["verbform_upos"]))
            entry[tagger] = {"counts": counts, "ranks": ranks(counts), "order": top_order(counts, pc["top_n"]),
                             **noun_verb_ratios(counts),
                             "verbforms": verbform_counts(words, upos_col, vf_col, set(pc["verbform_upos"])),
                             "point": statistics(para.sum(axis=0)),
                             "ci": bootstrap_ci(para, indices, pc["bootstrap_ci"])}
        entry["agreement"], entry["disagreements"] = agreement(words)
        data[text_id] = entry
    return data


def render_report(config: dict, data: dict) -> str:
    pc, labels, ids = config["pos_check"], config["corpus"]["labels"], list(data)
    tc = config["tag"]
    L = ["# Tagging report", "",
         "Generated by `uv run python -m huckfinn_cna.pos_check` from `data/interim/tagged/Ru#.tsv`",
         "(`uv run python -m huckfinn_cna.tag`). Counts and proportions only.", "",
         f"Taggers: Stanza {tc['stanza_version']} (models {tc['stanza_resources_version']}: "
         + ", ".join(f"{k} `{v}`" for k, v in tc["stanza_packages"].items())
         + f"); spaCy {tc['spacy_version']} with `{tc['spacy_model']}` {tc['spacy_model_version']}, "
           "given Stanza's tokens unchanged.",
         "Measures use **word tokens** only (letters, internal hyphens allowed), stop words kept.", ""]

    L += ["## Counts versus the data audit", "",
          "Audit: raw text, razdel sentences, Cyrillic word tokens. *Cleaned* applies the audit's word definition to",
          "the cleaned text. *Stanza* counts sentences per paragraph (no sentence crosses a paragraph) and word",
          "tokens as defined above (Latin-script words included).", "",
          "| Text | Paragraphs (audit) | Paragraphs (tagged) | Sentences (razdel, raw) | Sentences (Stanza) | "
          "Word tokens (audit, raw) | Word tokens (audit def., cleaned) | Word tokens (Stanza) |",
          "|---|---|---|---|---|---|---|---|"]
    for t in ids:
        d, a, m = data[t], data[t]["audit"], data[t]["meta"]
        L.append(f"| {t} | {a['paragraphs_non_empty_lines']} | {m['paragraphs']} | {a['sentences']} | {m['sentences']} | "
                 f"{a['word_tokens']} | {d['clean_words']} | {d['n_words']} |")

    for tagger in TAGGERS:
        all_tags = sorted({tag for t in ids for tag in data[t][tagger]["counts"]},
                          key=lambda tag: -sum(data[t][tagger]["counts"][tag] for t in ids))
        L += ["", f"## {tagger}: UPOS proportions of word tokens", "",
              "| UPOS | " + " | ".join(ids) + " |", "|---|" + "---|" * len(ids)]
        for tag in all_tags:
            L.append(f"| {tag} | " + " | ".join(pct(data[t][tagger]["counts"][tag], data[t]["n_words"]) for t in ids) + " |")
        L += ["", f"## {tagger}: top {pc['top_n']} UPOS, rank order", "",
              "| Text | " + " | ".join(str(i) for i in range(1, pc["top_n"] + 1)) + " |",
              "|---|" + "---|" * pc["top_n"]]
        for t in ids:
            L.append(f"| {t} | " + " | ".join(data[t][tagger]["order"]) + " |")
        L += ["", f"## {tagger}: NOUN : VERB", "",
              "| Text | NOUN | VERB | AUX | NOUN rank | VERB rank | NOUN : VERB | NOUN : (VERB + AUX) |",
              "|---|---|---|---|---|---|---|---|"]
        for t in ids:
            e = data[t][tagger]
            c, r = e["counts"], e["ranks"]
            L.append(f"| {t} | {c['NOUN']} | {c['VERB']} | {c['AUX']} | {r.get('NOUN', '—')} | {r.get('VERB', '—')} | "
                     f"{fmt(e['noun_verb'])} | {fmt(e['noun_verb_aux'])} |")
        forms = pc["verbforms"]
        L += ["", f"## {tagger}: VerbForm of {' and '.join(pc['verbform_upos'])} tokens", "",
              "Share of tokens tagged " + " or ".join(pc["verbform_upos"]) + "; *none* = no VerbForm feature.", "",
              "| Text | Tokens | " + " | ".join(forms) + " | other | none |", "|---|---|" + "---|" * (len(forms) + 2)]
        for t in ids:
            vf = data[t][tagger]["verbforms"]
            n = sum(vf.values())
            other = sum(v for k, v in vf.items() if k and k not in forms)
            L.append(f"| {t} | {n} | " + " | ".join(pct(vf[f], n) for f in forms) + f" | {pct(other, n)} | {pct(vf[''], n)} |")

    L += render_bootstrap(config, data)

    L += ["", "## Stanza–spaCy agreement on UPOS (word tokens)", "",
          "| Text | Word tokens | Same UPOS | Agreement |", "|---|---|---|---|"]
    for t in ids:
        n = data[t]["n_words"]
        L.append(f"| {t} | {n} | {n - sum(data[t]['disagreements'].values())} | {100 * data[t]['agreement']:.1f}% |")
    L += ["", f"### {pc['disagreement_top_n']} most common disagreements per text (Stanza / spaCy)", ""]
    for t in ids:
        n = data[t]["n_words"]
        L += [f"**{t}** ({labels[t]})", "", "| Stanza / spaCy | Tokens | Share of word tokens |", "|---|---|---|"]
        for (a, b), k in data[t]["disagreements"].most_common(pc["disagreement_top_n"]):
            L.append(f"| {a} / {b} | {k} | {pct(k, n)} |")
        L.append("")
    return "\n".join(L)


def fmt_stat(key: str, x: float) -> str:
    return f"{100 * x:.1f}%" if key in ("fin", "part", "conv") else f"{x:.3f}"


def fmt_ci(key: str, ci: tuple[float, float]) -> str:
    return f"[{fmt_stat(key, ci[0])}, {fmt_stat(key, ci[1])}]"


def comparisons(config: dict, data: dict) -> list[str]:
    """Plain statements: the compare_pair ratio intervals and the verb-form trend endpoints."""
    pc, labels = config["pos_check"], config["corpus"]["labels"]
    a, b = pc["compare_pair"]
    first, last = pc["trend_endpoints"]
    ids = list(data)
    out = []
    for tagger in TAGGERS:
        for key in ("noun_verb", "noun_verb_aux"):
            ca, cb = data[a][tagger]["ci"][key], data[b][tagger]["ci"][key]
            verdict = "overlap: not distinguishable" if overlap(ca, cb) else "do not overlap: distinguishable"
            out.append(f"- {tagger}, {STATISTICS[key][0]}: {labels[b]} {fmt_ci(key, cb)} vs {labels[a]} "
                       f"{fmt_ci(key, ca)} — intervals {verdict}.")
    for tagger in TAGGERS:
        for key in ("fin", "part", "conv"):
            cf, cl = data[first][tagger]["ci"][key], data[last][tagger]["ci"][key]
            ends = "do not overlap" if not overlap(cf, cl) else "overlap"
            adjacent = [f"{x}–{y}" for x, y in zip(ids, ids[1:])
                        if overlap(data[x][tagger]["ci"][key], data[y][tagger]["ci"][key])]
            out.append(f"- {tagger}, {STATISTICS[key][0]}: {first} {fmt_ci(key, cf)} vs {last} {fmt_ci(key, cl)} — "
                       f"endpoint intervals {ends}; adjacent pairs with overlapping intervals: "
                       f"{', '.join(adjacent) if adjacent else 'none'}.")
    return out


def render_bootstrap(config: dict, data: dict) -> list[str]:
    pc, ids = config["pos_check"], list(data)
    L = ["", f"## Bootstrap {100 * pc['bootstrap_ci']:.0f}% confidence intervals", "",
         f"Paragraphs resampled with replacement, {pc['bootstrap_resamples']:,} resamples, percentile intervals, "
         f"seed {pc['bootstrap_seed']}",
         "(numpy `default_rng([seed, text index])`; the same resampled paragraphs for both taggers). Verb-form "
         "shares are", f"shares of {' and '.join(pc['verbform_upos'])} tokens. Point estimate, then interval.", ""]
    for tagger in TAGGERS:
        L += [f"### {tagger}", "",
              "| Text | " + " | ".join(STATISTICS[k][0] for k in STATISTICS) + " |", "|---|" + "---|" * len(STATISTICS)]
        for t in ids:
            e = data[t][tagger]
            L.append(f"| {t} | " + " | ".join(f"{fmt_stat(k, e['point'][k])} {fmt_ci(k, e['ci'][k])}" for k in STATISTICS)
                     + " |")
        L.append("")
    L += ["### Comparisons (non-overlapping intervals)", ""] + comparisons(config, data)
    return L


# --- Figure ------------------------------------------------------------------------


def rank_heatmap(config: dict, data: dict, tagger: str, path: Path) -> list[str]:
    """Greyscale heatmap of each text's rank for the corpus-wide top tags (old Figure 6 layout)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    pc, labels, ids = config["pos_check"], config["corpus"]["labels"], list(data)
    tags = heatmap_tags({t: data[t][tagger]["counts"] for t in ids}, pc["top_n"])
    grid = np.array([[data[t][tagger]["ranks"].get(tag, np.nan) for tag in tags] for t in ids], dtype=float)
    vmax = max(pc["top_n"], np.nanmax(grid))

    fig, ax = plt.subplots(figsize=(12, 6.5))
    im = ax.imshow(grid, cmap="Greys", vmin=1, vmax=vmax, aspect="auto")
    for i in range(len(ids)):
        for j in range(len(tags)):
            v = grid[i, j]
            ax.text(j, i, "—" if np.isnan(v) else f"{v:.0f}", ha="center", va="center", fontsize=11,
                    color="white" if v > (1 + vmax) / 2 else "black")
    ax.set_xticks(range(len(tags)), tags, fontsize=11)
    ax.set_yticks(range(len(ids)), [labels[t] for t in ids], fontsize=11)
    ax.set_xticks(np.arange(-.5, len(tags)), minor=True)
    ax.set_yticks(np.arange(-.5, len(ids)), minor=True)
    ax.grid(which="minor", color="white", linewidth=1.5)
    ax.tick_params(which="both", length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_xlabel(f"Top {pc['top_n']} UPOS tags over all translations ({tagger}, word tokens, stop words kept)", fontsize=11)
    ax.set_ylabel("Translation", fontsize=11)
    ax.set_title(f"Relative Rank of Top {pc['top_n']} Part-of-Speech Tags ({tagger})", fontsize=14, pad=14)
    cbar = fig.colorbar(im, ax=ax, fraction=0.03, pad=0.02)
    cbar.set_label("Frequency rank (1 = most frequent)", fontsize=10)
    cbar.outline.set_visible(False)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=pc["figure_dpi"], bbox_inches="tight")
    plt.close(fig)
    return tags


def run(config_path: Path = DEFAULT_CONFIG) -> dict:
    config = load_config(config_path)
    data = build(config)
    report = repo_path(config, "tagging_report")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(render_report(config, data), encoding="utf-8")
    rank_heatmap(config, data, "Stanza", repo_path(config, "pos_rank_figure"))
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    data = run(args.config)
    for text_id, d in data.items():
        print(f"{text_id}: " + "; ".join(f"{tg} NOUN:VERB {fmt(d[tg]['noun_verb'])}, top 2 {d[tg]['order'][:2]}"
                                         for tg in TAGGERS))


if __name__ == "__main__":
    main()
