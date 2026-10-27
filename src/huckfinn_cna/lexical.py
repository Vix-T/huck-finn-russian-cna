"""Classic stylometric measures from data/interim/tagged/Ru#.tsv.

Word tokens only (is_word = 1), as Stanza lemmas lowercased and mapped ё -> е (the network-node rule).
Two views: "all" (stop words kept, primary) and "content" (UPOS in tag.content_upos). Measures: MSTTR,
MATTR, MTLD, HD-D, Yule's K, Honoré's R and unigram entropy, on whole texts and on equal-length
paragraph-aligned samples; words per sentence and punctuation per 1,000 words from Stanza's sentences;
paragraph-bootstrap intervals; a cross-check of MTLD, HD-D and MATTR against lexicalrichness; and the
effect of the old fusion of hyphenated words. Writes docs/classic/classic_report.md (tables only, no
text excerpts), data/interim/lexical/results.json and greyscale replacements for Figures 1-5.

Usage:
    uv run --frozen python -m huckfinn_cna.lexical [--config PATH]
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from huckfinn_cna.config import DEFAULT_CONFIG, load_config, repo_path
from huckfinn_cna.tag import read_tagged

# key: (label, higher value = richer vocabulary, decimals). MATTR keys are added per window from config.
BASE_MEASURES = {
    "msttr": ("MSTTR", True, 3),
    "mtld": ("MTLD", True, 1),
    "hdd": ("HD-D", True, 3),
    "yule_k": ("Yule's K", False, 2),
    "honore_r": ("Honoré's R", True, 1),
    "entropy": ("Unigram entropy (bits)", True, 3),
}

SENTENCE_MEASURES = {
    "mean": ("Mean words per sentence", 2),
    "median": ("Median words per sentence", 1),
    "iqr": ("IQR of words per sentence", 1),
    "punct_per_1000": ("Punctuation marks per 1,000 words", 1),
}


def measure_table(cfg: dict) -> dict:
    """Measure metadata in report order, with one MATTR entry per configured window."""
    out = {"msttr": BASE_MEASURES["msttr"]}
    for w in cfg["mattr_windows"]:
        out[f"mattr_{w}"] = (f"MATTR ({w})", True, 3)
    out.update({k: v for k, v in BASE_MEASURES.items() if k != "msttr"})
    return out


# --- Tokens ----------------------------------------------------------------------


def lemma_key(lemma: str, char_map: dict[str, str]) -> str:
    """Lowercased lemma with the network-node character map applied (ё -> е)."""
    return "".join(char_map.get(ch, ch) for ch in lemma.lower())


def fused_key(token: str, char_map: dict[str, str]) -> str:
    """Old-cleaning stand-in for a hyphenated word: lowercased surface form with hyphens deleted."""
    return lemma_key(token, char_map).replace("-", "")


@dataclass
class View:
    """Word tokens of one view as integer codes, in text order, with paragraph offsets."""
    codes: np.ndarray       # token codes into `types`
    bounds: np.ndarray      # tokens of paragraph p are codes[bounds[p]:bounds[p + 1]]
    types: list[str]

    @property
    def n(self) -> int:
        return len(self.codes)


def encode(keys: list[str], paras: list[int], n_paragraphs: int) -> View:
    index: dict[str, int] = {}
    codes = np.fromiter((index.setdefault(k, len(index)) for k in keys), dtype=np.int64, count=len(keys))
    bounds = np.searchsorted(np.asarray(paras, dtype=np.int64), np.arange(n_paragraphs + 1))
    return View(codes, bounds, list(index))


@dataclass
class Sentences:
    """Per-sentence counts in text order, with paragraph offsets into the sentence arrays."""
    words: np.ndarray       # word tokens (is_word = 1)
    punct: np.ndarray       # punctuation tokens (is_punct = 1)
    tokens: np.ndarray      # all tokens
    bounds: np.ndarray      # sentences of paragraph p are [bounds[p], bounds[p + 1])


def load_text(path: Path, n_paragraphs: int, char_map: dict[str, str], content_upos: set[str]) -> dict:
    """Views ("all", "content", and their hyphen-fused variants) and sentence counts of one text."""
    rows = read_tagged(path)
    keys = {"all": [], "content": [], "all_fused": [], "content_fused": []}
    paras = {"all": [], "content": []}
    hyphenated = Counter()
    sent: dict[int, list[int]] = {}       # sent_id -> [para, words, punct, tokens]
    for r in rows:
        para = int(r["para_id"]) - 1
        s = sent.setdefault(int(r["sent_id"]), [para, 0, 0, 0])
        s[3] += 1
        s[2] += r["is_punct"] == "1"
        if r["is_word"] != "1":
            continue
        s[1] += 1
        key = lemma_key(r["lemma"], char_map)
        fused = fused_key(r["token"], char_map) if "-" in r["token"] else key
        views = ["all"] + (["content"] if r["upos"] in content_upos else [])
        for v in views:
            hyphenated[v] += "-" in r["token"]
            keys[v].append(key)
            keys[f"{v}_fused"].append(fused)
            paras[v].append(para)
    out = {v: encode(keys[v], paras[v.removesuffix("_fused")], n_paragraphs) for v in keys}
    table = np.array([sent[k] for k in sorted(sent)], dtype=np.int64)
    out["sentences"] = Sentences(words=table[:, 1], punct=table[:, 2], tokens=table[:, 3],
                                 bounds=np.searchsorted(table[:, 0], np.arange(n_paragraphs + 1)))
    out["hyphenated"] = {v: hyphenated[v] for v in paras}
    return out


# --- Measures ----------------------------------------------------------------------


def frequencies(codes: np.ndarray, vocab_size: int | None = None) -> np.ndarray:
    """Frequency of each type that occurs."""
    counts = np.bincount(codes, minlength=vocab_size or 0)
    return counts[counts > 0]


def msttr(codes: np.ndarray, chunk: int, min_last_fraction: float) -> float:
    """Mean TTR over consecutive chunks; the old code's rule drops a final partial chunk shorter than
    min_last_fraction of a chunk (unless it is the only chunk)."""
    chunks = [codes[i:i + chunk] for i in range(0, len(codes), chunk)]
    if len(chunks) > 1 and len(chunks[-1]) < chunk * min_last_fraction:
        chunks.pop()
    return float(np.mean([len(np.unique(c)) / len(c) for c in chunks]))


def previous_occurrence(codes: np.ndarray) -> np.ndarray:
    """Index of the previous occurrence of the same code (-1 if none)."""
    order = np.argsort(codes, kind="stable")
    sorted_codes = codes[order]
    prev = np.full(len(codes), -1, dtype=np.int64)
    same = sorted_codes[1:] == sorted_codes[:-1]
    prev[order[1:][same]] = order[:-1][same]
    return prev


def mattr(codes: np.ndarray, window: int, prev: np.ndarray | None = None) -> float:
    """Moving-average TTR over every window of `window` consecutive tokens (Covington & McFall 2010).

    Token i counts as a new type in the windows starting at s with prev[i] < s <= i, s <= n - window,
    s > i - window, so the sum of types over all windows is computed without visiting each window."""
    n = len(codes)
    if not 1 <= window <= n:
        raise ValueError(f"window {window} must be between 1 and the text length {n}")
    if prev is None:
        prev = previous_occurrence(codes)
    i = np.arange(n)
    lo = np.maximum(prev + 1, i - window + 1)
    hi = np.minimum(i, n - window)
    return float(np.clip(hi - lo + 1, 0, None).sum() / (window * (n - window + 1)))


def mtld_pass(seq: list[int], threshold: float, vocab_size: int) -> float:
    """One direction of MTLD: tokens / factors. A factor ends when the running TTR is <= threshold; the
    final partial factor counts (1 - TTR) / (1 - threshold) (McCarthy & Jarvis 2010)."""
    stamp = [0] * vocab_size
    generation, n, types, factors, ttr = 1, 0, 0, 0.0, 1.0
    for x in seq:
        n += 1
        if stamp[x] != generation:
            stamp[x] = generation
            types += 1
        ttr = types / n
        if ttr <= threshold:
            factors += 1
            generation += 1
            n = types = 0
    if n > 0:
        factors += (1 - ttr) / (1 - threshold)
    if factors == 0:                  # TTR stayed at 1 throughout: one factor (as in lexicalrichness)
        factors = 1.0
    return len(seq) / factors


def mtld(codes: np.ndarray, threshold: float) -> float:
    """Bidirectional MTLD: mean of the forward and backward passes."""
    seq = codes.tolist()
    vocab = int(codes.max()) + 1 if len(seq) else 0
    return (mtld_pass(seq, threshold, vocab) + mtld_pass(seq[::-1], threshold, vocab)) / 2


def hdd(freqs: np.ndarray, n: int, draws: int) -> float:
    """HD-D: sum over types of P(type occurs at least once in `draws` tokens drawn without replacement)
    / draws (McCarthy & Jarvis 2007). P(0) = prod_k (n - f - k) / (n - k), k = 0 .. draws - 1."""
    if not 1 <= draws <= n:
        raise ValueError(f"draws {draws} must be between 1 and the text length {n}")
    values, mult = np.unique(freqs, return_counts=True)
    k = np.arange(draws)
    p0 = np.prod(np.clip((n - values[:, None] - k) / (n - k), 0, None), axis=1)
    return float(((1 - p0) * mult).sum() / draws)


def yule_k(freqs: np.ndarray, n: int, scale: float) -> float:
    """Yule's K = scale * (sum_i i^2 V_i - N) / N^2, as in the old code (sum_i i^2 V_i = sum of f^2)."""
    return float(scale * ((freqs.astype(np.float64) ** 2).sum() - n) / n ** 2)


def honore_r(freqs: np.ndarray, n: int, scale: float) -> float:
    """Honoré's R = scale * ln N / (1 - V1 / V); NaN when every type is a hapax."""
    v, v1 = len(freqs), int((freqs == 1).sum())
    return float("nan") if v1 == v else float(scale * math.log(n) / (1 - v1 / v))


def entropy(freqs: np.ndarray, n: int, base: float) -> float:
    p = freqs / n
    return float(-(p * np.log(p)).sum() / math.log(base))


def measures(codes: np.ndarray, cfg: dict, vocab_size: int | None = None) -> dict[str, float]:
    """Every lexical measure of one token sequence."""
    n = len(codes)
    freqs = frequencies(codes, vocab_size)
    prev = previous_occurrence(codes)
    out = {"msttr": msttr(codes, cfg["msttr_chunk"], cfg["msttr_min_last_fraction"])}
    for w in cfg["mattr_windows"]:
        out[f"mattr_{w}"] = mattr(codes, w, prev)
    out["mtld"] = mtld(codes, cfg["mtld_threshold"])
    out["hdd"] = hdd(freqs, n, cfg["hdd_draws"])
    out["yule_k"] = yule_k(freqs, n, cfg["yule_scale"])
    out["honore_r"] = honore_r(freqs, n, cfg["honore_scale"])
    out["entropy"] = entropy(freqs, n, cfg["entropy_base"])
    return out


def sentence_measures(words: np.ndarray, punct_total: int, method: str) -> dict[str, float]:
    """Words per sentence (sentences with no word token excluded) and punctuation per 1,000 words."""
    w = words[words > 0]
    q1, median, q3 = np.quantile(w, [0.25, 0.5, 0.75], method=method)
    return {"mean": float(w.mean()), "median": float(median), "iqr": float(q3 - q1),
            "punct_per_1000": float(1000 * punct_total / words.sum())}


# --- Samples and bootstrap -----------------------------------------------------------


def sample_length(counts: list[int], rounding: int) -> int:
    """The shortest count rounded down to a multiple of `rounding`."""
    return min(counts) // rounding * rounding


def paragraph_starts(bounds: np.ndarray) -> np.ndarray:
    """Token offsets at which a paragraph with at least one token of the view starts."""
    starts, ends = bounds[:-1], bounds[1:]
    return starts[ends > starts]


def nonoverlapping_starts(starts: np.ndarray, n_tokens: int, length: int) -> list[int]:
    """Consecutive samples from the first paragraph, each starting at the first paragraph start at or
    after the end of the previous sample."""
    out, s = [], int(starts[0]) if len(starts) else 0
    while s + length <= n_tokens:
        out.append(s)
        later = starts[starts >= s + length]
        if not len(later):
            break
        s = int(later[0])
    return out


def random_starts(starts: np.ndarray, n_tokens: int, length: int, k: int, rng) -> np.ndarray:
    """k paragraph starts drawn uniformly with replacement from those that leave `length` tokens."""
    valid = starts[starts + length <= n_tokens]
    return rng.choice(valid, size=k, replace=True)


def rng_for(cfg: dict, stream: int, text_index: int):
    return np.random.default_rng([cfg["master_seed"], stream, text_index])


def gather(bounds: np.ndarray, paragraphs: np.ndarray) -> np.ndarray:
    """Concatenated positions [bounds[p], bounds[p + 1]) for each p in `paragraphs`, in order."""
    starts = bounds[paragraphs]
    lengths = bounds[paragraphs + 1] - starts
    shift = np.repeat(starts - (np.cumsum(lengths) - lengths), lengths)
    return shift + np.arange(lengths.sum())


def percentile_ci(values: np.ndarray, level: float, method: str) -> tuple[float, float]:
    alpha = (1 - level) / 2
    lo, hi = np.quantile(values, [alpha, 1 - alpha], method=method)
    return float(lo), float(hi)


# --- Per-text work (run in a worker process) --------------------------------------


def lexicalrichness_values(view: View, cfg: dict) -> dict[str, float]:
    from lexicalrichness import LexicalRichness
    lex = LexicalRichness([view.types[c] for c in view.codes], preprocessor=None, tokenizer=None)
    out = {"mtld": lex.mtld(threshold=cfg["mtld_threshold"]), "hdd": lex.hdd(draws=cfg["hdd_draws"])}
    for w in cfg["mattr_windows"]:
        out[f"mattr_{w}"] = lex.mattr(window_size=w)
    return out


def analyse_text(job: tuple) -> dict:
    """Everything computed for one text: whole-text values, samples, bootstrap and cross-check."""
    config, text_index, text_id, n_paragraphs, lengths = job
    cfg = config["lexical"]
    data = load_text(repo_path(config, "tagged_dir") / f"{text_id}.tsv", n_paragraphs,
                     config["networks"]["lemma_char_map"], set(config["tag"]["content_upos"]))
    sents: Sentences = data["sentences"]
    method = cfg["quantile_method"]
    out = {"text": text_id, "paragraphs": n_paragraphs, "sentences": {}, "views": {}}

    for view_name in cfg["views"]:
        view: View = data[view_name]
        fused: View = data[f"{view_name}_fused"]
        starts = paragraph_starts(view.bounds)
        length = lengths[view_name]
        nonover = nonoverlapping_starts(starts, view.n, length)
        if text_id in cfg["single_sample_texts"]:
            nonover, rand = nonover[:1], np.array([], dtype=np.int64)
        else:
            rand = random_starts(starts, view.n, length, cfg["random_windows"],
                                 rng_for(cfg, cfg["windows_stream"], text_index))
        out["views"][view_name] = {
            "tokens": view.n, "types": len(view.types),
            "hyphen_fused_tokens": data["hyphenated"][view_name],
            "whole": measures(view.codes, cfg, len(view.types)),
            "fused": measures(fused.codes, cfg, len(fused.types)),
            "sample_length": length,
            "nonoverlapping_starts": nonover,
            "nonoverlapping": [measures(view.codes[s:s + length], cfg, len(view.types)) for s in nonover],
            "random_starts": [int(s) for s in rand],
            "random": [measures(view.codes[s:s + length], cfg, len(view.types)) for s in rand],
            "lexicalrichness": lexicalrichness_values(view, cfg),
        }

    out["sentences"]["whole"] = sentence_measures(sents.words, int(sents.punct.sum()), method)
    nonempty = sents.words > 0
    out["sentences"]["counts"] = {"sentences": int(len(sents.words)), "no_word_sentences": int((~nonempty).sum()),
                                  "words": int(sents.words.sum()), "punct": int(sents.punct.sum()),
                                  "tokens_per_sentence_incl_punct": float(sents.tokens[nonempty].mean()),
                                  "words_punct_per_sentence": float((sents.words + sents.punct)[nonempty].mean())}

    # Bootstrap: one draw of paragraph indices per text, shared by both views and sentence length.
    rng = rng_for(cfg, cfg["bootstrap_stream"], text_index)
    draws = rng.integers(0, n_paragraphs, size=(cfg["bootstrap_resamples"], n_paragraphs))
    boot = {v: {k: [] for k in out["views"][v]["whole"]} for v in cfg["views"]}
    boot_sent = {k: [] for k in SENTENCE_MEASURES}
    for paragraphs in draws:
        for v in cfg["views"]:
            view = data[v]
            for k, x in measures(view.codes[gather(view.bounds, paragraphs)], cfg, len(view.types)).items():
                boot[v][k].append(x)
        idx = gather(sents.bounds, paragraphs)
        for k, x in sentence_measures(sents.words[idx], int(sents.punct[idx].sum()), method).items():
            boot_sent[k].append(x)
    for v in cfg["views"]:
        out["views"][v]["ci"] = {k: percentile_ci(np.array(xs), cfg["bootstrap_ci"], method) for k, xs in boot[v].items()}
        out["views"][v]["boot_mean"] = {k: float(np.mean(xs)) for k, xs in boot[v].items()}
        out["views"][v]["boot_sd"] = {k: float(np.std(xs, ddof=1)) for k, xs in boot[v].items()}
    out["sentences"]["ci"] = {k: percentile_ci(np.array(xs), cfg["bootstrap_ci"], method) for k, xs in boot_sent.items()}
    return out


# --- Summaries -----------------------------------------------------------------------


def richness_order(values: dict[str, float], higher_is_richer: bool) -> list[str]:
    """Text ids, richest first."""
    return sorted(values, key=lambda t: -values[t] if higher_is_richer else values[t])


def summarize_samples(entry: dict, key: str, cfg: dict) -> dict:
    nonover = [m[key] for m in entry["nonoverlapping"]]
    rand = np.array([m[key] for m in entry["random"]])
    out = {"nonoverlapping_mean": float(np.mean(nonover)), "k": len(nonover)}
    if len(rand):
        lo, hi = percentile_ci(rand, cfg["bootstrap_ci"], cfg["quantile_method"])
        out.update(random_mean=float(rand.mean()), random_lo=lo, random_hi=hi)
    else:
        out.update(random_mean=out["nonoverlapping_mean"], random_lo=None, random_hi=None)
    return out


def rank_comparison(results: dict, view: str, cfg: dict) -> dict:
    """Per measure: richness order on whole texts, at equal length (mean of non-overlapping samples)
    and by the mean of the random windows."""
    out = {}
    for key, (_, higher, _) in measure_table(cfg).items():
        whole = {t: r["views"][view]["whole"][key] for t, r in results.items()}
        eq = {t: summarize_samples(r["views"][view], key, cfg) for t, r in results.items()}
        o_whole = richness_order(whole, higher)
        o_eq = richness_order({t: e["nonoverlapping_mean"] for t, e in eq.items()}, higher)
        o_rand = richness_order({t: e["random_mean"] for t, e in eq.items()}, higher)
        out[key] = {"whole": o_whole, "equal": o_eq, "random": o_rand,
                    "same": o_whole == o_eq, "same_random": o_eq == o_rand}
    return out


def crosscheck_rows(results: dict, cfg: dict) -> list[dict]:
    rows = []
    for t, r in results.items():
        for v in cfg["views"]:
            e = r["views"][v]
            for key, theirs in e["lexicalrichness"].items():
                ours = e["whole"][key]
                rel = abs(ours - theirs) / abs(theirs) if theirs else abs(ours)
                rows.append({"text": t, "view": v, "measure": key, "ours": ours, "theirs": theirs, "rel": rel,
                             "agree": rel <= cfg["crosscheck_rel_tol"]})
    return rows


# --- Report --------------------------------------------------------------------------


def f(x: float | None, d: int) -> str:
    return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:,.{d}f}"


def render_report(config: dict, results: dict) -> str:
    cfg, labels, ids = config["lexical"], config["corpus"]["labels"], list(results)
    meas = measure_table(cfg)
    level = f"{100 * cfg['bootstrap_ci']:.0f}%"
    L = ["# Classic stylometric measures", "",
         "Generated by `uv run --frozen python -m huckfinn_cna.lexical` from `data/interim/tagged/Ru#.tsv`.",
         "Tables only. Tokens: word tokens (`is_word = 1`), Stanza lemma lowercased, ё → е (the network-node rule).",
         f"Views: **all** (stop words kept; primary) and **content** (Stanza UPOS in {', '.join(config['tag']['content_upos'])}).",
         "",
         f"Parameters: MSTTR chunk {cfg['msttr_chunk']:,} (final partial chunk dropped if shorter than "
         f"{cfg['msttr_min_last_fraction']:g} of a chunk); MATTR windows {', '.join(str(w) for w in cfg['mattr_windows'])} "
         f"(primary {cfg['mattr_primary']}); MTLD bidirectional, threshold {cfg['mtld_threshold']}; HD-D sample "
         f"{cfg['hdd_draws']}; Yule's K = {cfg['yule_scale']:,} · (Σ i² V_i − N) / N²; Honoré's R = "
         f"{cfg['honore_scale']} · ln N / (1 − V1/V); entropy log{cfg['entropy_base']}.",
         f"Bootstrap: paragraphs resampled with replacement, {cfg['bootstrap_resamples']:,} resamples, percentile "
         f"{level} intervals; master seed {cfg['master_seed']}, generator numpy `default_rng([{cfg['master_seed']}, "
         f"stream, text index])`, stream {cfg['bootstrap_stream']} = bootstrap, {cfg['windows_stream']} = random windows.",
         ""]

    L += ["## Tokens and equal-length samples", "",
          "N = shortest text's token count in the view, rounded down to a multiple of "
          f"{cfg['sample_round']:,}. Samples start at a paragraph start and take exactly N tokens.", "",
          "| Text | View | Tokens | Types | N | Non-overlapping samples | Random windows |", "|---|---|---|---|---|---|---|"]
    for t in ids:
        for v in cfg["views"]:
            e = results[t]["views"][v]
            L.append(f"| {t} | {v} | {e['tokens']:,} | {e['types']:,} | {e['sample_length']:,} | "
                     f"{len(e['nonoverlapping'])} | {len(e['random'])} |")

    rows = crosscheck_rows(results, cfg)
    L += ["", "## Cross-check against lexicalrichness", "",
          f"lexicalrichness given the same token lists. Tolerance: relative difference ≤ {cfg['crosscheck_rel_tol']:g}. "
          f"Agreement: {sum(r['agree'] for r in rows)} of {len(rows)} values.", "",
          "| Text | View | Measure | lexical.py | lexicalrichness | Relative difference |", "|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['text']} | {r['view']} | {meas[r['measure']][0]} | {r['ours']:.10g} | {r['theirs']:.10g} | {r['rel']:.1e} |")

    for v in cfg["views"]:
        boot_labels = ", ".join(meas[k][0] for k in cfg["bootstrap_measures"])
        L += ["", f"## Whole texts, view {v}", "",
              f"Point estimate; for {boot_labels}, also the paragraph-bootstrap {level} interval. The other measures have no "
              "bootstrap interval (biased; see the diagnostic below); their uncertainty is the spread of the equal-length windows.", "",
              "| Measure | " + " | ".join(ids) + " |", "|---|" + "---|" * len(ids)]
        for key, (label, higher, d) in meas.items():
            cells = []
            for t in ids:
                e = results[t]["views"][v]
                cell = f(e["whole"][key], d)
                if key in cfg["bootstrap_measures"]:
                    lo, hi = e["ci"][key]
                    cell += f" [{f(lo, d)}, {f(hi, d)}]"
                cells.append(cell)
            L.append(f"| {label} ({'higher' if higher else 'lower'} = richer) | " + " | ".join(cells) + " |")

        L += ["", f"## Equal-length samples, view {v}", "",
              f"Mean over the non-overlapping samples (k = number of samples); then the mean of the random windows "
              f"and their central {level} range. {', '.join(cfg['single_sample_texts'])}: one sample from the start of the text.", "",
              "| Measure | " + " | ".join(ids) + " |", "|---|" + "---|" * len(ids)]
        for key, (label, _, d) in meas.items():
            cells = []
            for t in ids:
                s = summarize_samples(results[t]["views"][v], key, cfg)
                c = f"{f(s['nonoverlapping_mean'], d)} (k={s['k']})"
                if s["random_lo"] is not None:
                    c += f"; {f(s['random_mean'], d)} [{f(s['random_lo'], d)}, {f(s['random_hi'], d)}]"
                cells.append(c)
            L.append(f"| {label} | " + " | ".join(cells) + " |")

        ranks = rank_comparison(results, v, cfg)
        L += ["", f"## Rank order, view {v} (richest first)", "",
              "*Equal length* ranks the mean of the non-overlapping samples; *random* ranks the mean of the random windows.", "",
              "| Measure | Whole texts | Equal length | Same order? | Random windows | Same as equal length? |",
              "|---|---|---|---|---|---|"]
        for key, r in ranks.items():
            L.append(f"| {meas[key][0]} | {' > '.join(r['whole'])} | {' > '.join(r['equal'])} | "
                     f"{'yes' if r['same'] else 'no'} | {' > '.join(r['random'])} | {'yes' if r['same_random'] else 'no'} |")

    L += ["", "## Sentence length (Stanza sentences)", "",
          "Words per sentence counts word tokens only; sentences with no word token are excluded. Punctuation: "
          "tokens whose every character is punctuation. "
          f"Point estimate and bootstrap {level} interval. Quantiles: numpy method `{cfg['quantile_method']}`.", "",
          "| Text | Sentences | Without a word | " + " | ".join(lbl for lbl, _ in SENTENCE_MEASURES.values()) + " |",
          "|---|---|---|" + "---|" * len(SENTENCE_MEASURES)]
    for t in ids:
        s = results[t]["sentences"]
        cells = [f"{f(s['whole'][k], d)} [{f(s['ci'][k][0], d)}, {f(s['ci'][k][1], d)}]" for k, (_, d) in SENTENCE_MEASURES.items()]
        L.append(f"| {t} | {s['counts']['sentences']:,} | {s['counts']['no_word_sentences']} | " + " | ".join(cells) + " |")
    L += ["", "Bridge to the old definition (same sentences): mean tokens per sentence with punctuation counted.", "",
          "| Text | Words only | Words + punctuation | All tokens |", "|---|---|---|---|"]
    for t in ids:
        s = results[t]["sentences"]
        L.append(f"| {t} | {s['whole']['mean']:.2f} | {s['counts']['words_punct_per_sentence']:.2f} | "
                 f"{s['counts']['tokens_per_sentence_incl_punct']:.2f} |")

    L += render_old_new(config, results)
    L += render_bootstrap_diagnostic(config, results)

    L += ["", "## Sensitivity: the old fusion of hyphenated words", "",
          "Each hyphenated word token is replaced by its lowercased surface form with the hyphen deleted (e.g. "
          "кое-что → коечто), unlemmatized, approximating the old cleaning; all other tokens unchanged. Whole texts.", ""]
    for v in cfg["views"]:
        L += [f"View {v}:", "", "| Measure | " + " | ".join(f"{t} new → fused" for t in ids) + " |",
              "|---|" + "---|" * len(ids),
              "| Hyphenated tokens | " + " | ".join(f"{results[t]['views'][v]['hyphen_fused_tokens']:,}" for t in ids) + " |"]
        for key, (label, _, d) in meas.items():
            L.append(f"| {label} | " + " | ".join(
                f"{f(results[t]['views'][v]['whole'][key], d)} → {f(results[t]['views'][v]['fused'][key], d)}" for t in ids) + " |")
        L.append("")
    return "\n".join(L)


def render_bootstrap_diagnostic(config: dict, results: dict) -> list[str]:
    """Paragraph-bootstrap behaviour of every lexical measure: bias against the point estimate."""
    cfg, ids = config["lexical"], list(results)
    meas = measure_table(cfg)
    level = f"{100 * cfg['bootstrap_ci']:.0f}%"
    L = ["", "## Diagnostic: paragraph bootstrap of the lexical measures", "",
         f"Bias = bootstrap mean − point estimate; SE = bootstrap SD. *Inside* = the point estimate lies in the {level} "
         "percentile interval. Intervals are reported only for " + ", ".join(meas[k][0] for k in cfg["bootstrap_measures"]) + ".", ""]
    for v in cfg["views"]:
        L += [f"View {v}:", "", "| Measure | Text | Point | Bootstrap mean | Bias / SE | Interval | Inside |",
              "|---|---|---|---|---|---|---|"]
        for key, (label, _, d) in meas.items():
            for t in ids:
                e = results[t]["views"][v]
                point, mean, sd = e["whole"][key], e["boot_mean"][key], e["boot_sd"][key]
                lo, hi = e["ci"][key]
                ratio = (mean - point) / sd if sd else float("nan")
                L.append(f"| {label} | {t} | {f(point, d)} | {f(mean, d)} | {ratio:+.1f} | [{f(lo, d)}, {f(hi, d)}] | "
                         f"{'yes' if lo <= point <= hi else 'no'} |")
        L.append("")
    return L


OLD_MEASURES = {   # config key in [lexical.old] -> (new measure key, label, decimals, old basis)
    "yule_k": ("yule_k", "Yule's K", 2, "lemmas, stop words removed"),
    "honore": ("honore_r", "Honoré's R (old label \"W\")", 1, "lemmas, stop words removed"),
    "msttr": ("msttr", "Chunked TTR (MSTTR)", 3, "lemmas, stop words removed"),
    "entropy": ("entropy", "Unigram entropy", 3, "lemmas, stop words kept"),
}


def render_old_new(config: dict, results: dict) -> list[str]:
    cfg, ids, old = config["lexical"], list(results), config["lexical"]["old"]
    L = ["", "## Old versus new", "",
         f"Old: exact values from the old result CSVs ({old['source']}); *figure label* = the bar label of Figures 1–5 of "
         "Final Report 3. The old values used the old cleaning (ё deleted, hyphenated words fused, OCR splits unrepaired) and "
         "pymorphy2 lemmas of that text.", ""]
    for key, (new_key, label, d, basis) in OLD_MEASURES.items():
        L += [f"### {label} (old basis: {basis})", "",
              "| Text | Old (exact) | Figure label | New, all: whole | New, all: equal length | New, content: whole | "
              "New, content: equal length |",
              "|---|---|---|---|---|---|---|"]
        for i, t in enumerate(ids):
            a, c = results[t]["views"]["all"], results[t]["views"]["content"]
            L.append(f"| {t} | {f(old[key][t], d + 2)} | {old['figure_labels'][key][i]} | {f(a['whole'][new_key], d)} | "
                     f"{f(summarize_samples(a, new_key, cfg)['nonoverlapping_mean'], d)} | {f(c['whole'][new_key], d)} | "
                     f"{f(summarize_samples(c, new_key, cfg)['nonoverlapping_mean'], d)} |")
        L.append("")
    L += ["### Average sentence length (old: NLTK sentences of the raw text, every NLTK token counted, punctuation included)", "",
          "| Text | Old (exact) | Figure label | New: mean words per sentence | New: words + punctuation per sentence |",
          "|---|---|---|---|---|"]
    for i, t in enumerate(ids):
        s = results[t]["sentences"]
        L.append(f"| {t} | {old['sentence_length'][t]:.4f} | {old['figure_labels']['sentence_length'][i]} | "
                 f"{s['whole']['mean']:.2f} | {s['counts']['words_punct_per_sentence']:.2f} |")
    return L


# --- Figures ---------------------------------------------------------------------------


def _ranges(ax, positions, ranges, cap: float, vertical: bool) -> None:
    """Draw each (lo, hi) range as a capped line at its bar position, independent of the bar's height
    (a single equal-length sample can lie outside the range of the random windows)."""
    for p, (lo, hi) in zip(positions, ranges or []):
        if lo is None:
            continue
        line, caps = (ax.vlines, ax.hlines) if vertical else (ax.hlines, ax.vlines)
        line(p, lo, hi, color="black", linewidth=1, zorder=3)
        caps([lo, hi], p - cap, p + cap, color="black", linewidth=1, zorder=3)


def _label_anchor(values, ranges) -> list[float]:
    """Where each bar's value label goes: past the bar's end or its range line, whichever is further."""
    ranges = ranges or [(None, None)] * len(values)
    return [max(v, hi) if hi is not None else v for v, (_, hi) in zip(values, ranges)]


def _legend(ax) -> None:
    ax.legend(frameon=False, fontsize=10, loc="lower center", bbox_to_anchor=(0.5, 1.0), ncol=2)


def _vertical(ax, labels, values, errors, colors, names, fmt, title, ylabel, hatches=None):
    """Grouped vertical bars in the old seaborn whitegrid style, each bar labelled."""
    n_groups = len(values)
    width = 0.8 / n_groups
    x = np.arange(len(labels))
    for g in range(n_groups):
        pos = x - 0.4 + width * (g + 0.5)
        ax.bar(pos, values[g], width, color=colors[g], edgecolor="black", label=names[g],
                      hatch=hatches[g] if hatches else None)
        _ranges(ax, pos, errors[g], width / 4, vertical=True)
        for p, v, end in zip(pos, values[g], _label_anchor(values[g], errors[g])):
            ax.annotate(fmt.format(v), (p, end), xytext=(0, 3), textcoords="offset points", ha="center", va="bottom",
                        fontsize=9)
    ax.set_xticks(x, labels, rotation=45, ha="right")
    ax.set_title(title, fontsize=16, pad=30)
    ax.set_xlabel("Translation", fontsize=12)
    ax.set_ylabel(ylabel, fontsize=12)
    ax.yaxis.grid(True, color="#d9d9d9")
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_color("#cccccc")
    if n_groups > 1:
        _legend(ax)


def _horizontal(ax, labels, values, errors, colors, names, fmt, title, xlabel, hatches=None):
    """Grouped horizontal dark-grey bars in the old Figure 3/5 style, each bar labelled."""
    n_groups = len(values)
    height = 0.8 / n_groups
    y = np.arange(len(labels))
    for g in range(n_groups):
        pos = y - 0.4 + height * (g + 0.5)
        ax.barh(pos, values[g], height, color=colors[g], edgecolor="black", label=names[g],
                       hatch=hatches[g] if hatches else None)
        _ranges(ax, pos, errors[g], height / 4, vertical=False)
        for p, v, end in zip(pos, values[g], _label_anchor(values[g], errors[g])):
            ax.annotate(fmt.format(v), (end, p), xytext=(5, 0), textcoords="offset points", ha="left", va="center",
                        fontsize=9)
    ax.set_yticks(y, labels)
    ax.invert_yaxis()
    ax.set_title(title, fontsize=16, pad=30)
    ax.set_ylabel("Translation", fontsize=12)
    ax.set_xlabel(xlabel, fontsize=12)
    if n_groups > 1:
        _legend(ax)


def figures(config: dict, results: dict) -> list[Path]:
    """Greyscale replacements for Figures 1-5 (primary view): whole text with bootstrap interval and
    equal length with the random-window range; sentence length with its bootstrap interval."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    cfg, ids = config["lexical"], list(results)
    labels = [config["corpus"]["labels"][t] for t in ids]
    v = cfg["primary_view"]
    dpi = config["pos_check"]["figure_dpi"]
    out_dir = repo_path(config, "figures_dir")
    out_dir.mkdir(parents=True, exist_ok=True)
    n_eq = results[ids[0]]["views"][v]["sample_length"]
    level = f"{100 * cfg['bootstrap_ci']:.0f}%"

    def names(key):
        whole = f"Whole text ({level} bootstrap interval)" if key in cfg["bootstrap_measures"] else "Whole text"
        return [whole, f"Equal length, {n_eq:,} words ({level} range of windows)"]

    def pair(key):
        whole = [results[t]["views"][v]["whole"][key] for t in ids]
        whole_ci = [tuple(results[t]["views"][v]["ci"][key]) if key in cfg["bootstrap_measures"] else (None, None)
                    for t in ids]
        eq = [summarize_samples(results[t]["views"][v], key, cfg) for t in ids]
        return [whole, [e["nonoverlapping_mean"] for e in eq]], [whole_ci, [(e["random_lo"], e["random_hi"]) for e in eq]]

    specs = [
        ("fig1_yules_k_greyscale.png", "yule_k", "v", "{:.1f}", "Yule's K",
         "Yule's K (Lower = Richer Lexical Diversity)", ["silver", "white"]),
        ("fig2_honores_r_greyscale.png", "honore_r", "v", "{:.0f}", "Honoré's R",
         "Honoré's R (Higher = Richer Lexical Diversity)", ["gainsboro", "white"]),
        ("fig3_msttr_greyscale.png", "msttr", "h", "{:.3f}", "Lexical Richness (MSTTR, 2,000-word chunks) by Translation",
         "MSTTR (High = More varied vocabulary)", ["dimgrey", "white"]),
        ("fig5_unigram_entropy_greyscale.png", "entropy", "h", "{:.3f}", "Lexical Diversity (Unigram Entropy) by Translation",
         "Unigram entropy, bits (High = More diverse, less repetitive)", ["dimgrey", "white"]),
    ]
    paths = []
    for fname, key, orient, fmt, title, axis_label, colors in specs:
        values, errors = pair(key)
        fig, ax = plt.subplots(figsize=(10, 6.5) if orient == "v" else (10, 6))
        draw = _vertical if orient == "v" else _horizontal
        draw(ax, labels, values, errors, colors, names(key), fmt, title, axis_label, hatches=[None, "//"])
        if orient == "h":
            ax.set_xlim(0, max(max(g) for g in values) * 1.18)
        else:
            ax.set_ylim(0, max(max(g) for g in values) * 1.15)
        fig.tight_layout()
        paths.append(out_dir / fname)
        fig.savefig(paths[-1], dpi=dpi, bbox_inches="tight")
        plt.close(fig)

    s = [results[t]["sentences"] for t in ids]
    fig, ax = plt.subplots(figsize=(10, 6))
    _vertical(ax, labels, [[x["whole"]["mean"] for x in s]], [[tuple(x["ci"]["mean"]) for x in s]], ["lightgrey"],
              [f"Mean ({level} bootstrap interval)"], "{:.1f}", "Average Sentence Length",
              "Average Words per Sentence (punctuation not counted)")
    ax.set_ylim(0, max(x["whole"]["mean"] for x in s) * 1.15)
    fig.tight_layout()
    paths.append(out_dir / "fig4_sentence_length_greyscale.png")
    fig.savefig(paths[-1], dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return paths


# --- Run ---------------------------------------------------------------------------------


def to_jsonable(obj):
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, np.generic):
        return obj.item()
    if isinstance(obj, float) and math.isnan(obj):
        return None
    return obj


def count_tokens(config: dict, text_id: str) -> dict[str, int]:
    """Tokens per view, for the equal-sample length (read before the per-text jobs start)."""
    content = set(config["tag"]["content_upos"])
    counts = Counter()
    for r in read_tagged(repo_path(config, "tagged_dir") / f"{text_id}.tsv"):
        if r["is_word"] == "1":
            counts["all"] += 1
            counts["content"] += r["upos"] in content
    return dict(counts)


def build(config: dict) -> dict:
    cfg, texts = config["lexical"], config["corpus"]["texts"]
    tagged = repo_path(config, "tagged_dir")
    counts = {t: count_tokens(config, t) for t in texts}
    lengths = {v: sample_length([counts[t][v] for t in texts], cfg["sample_round"]) for v in cfg["views"]}
    jobs = [(config, i, t, json.loads((tagged / f"{t}.meta.json").read_text(encoding="utf-8"))["paragraphs"], lengths)
            for i, t in enumerate(texts)]
    if cfg["workers"] == 1:           # sequential; also the only option where semaphores are unavailable
        return {r["text"]: r for r in map(analyse_text, jobs)}
    with ProcessPoolExecutor(max_workers=cfg["workers"]) as pool:
        return {r["text"]: r for r in pool.map(analyse_text, jobs)}


def run(config_path: Path = DEFAULT_CONFIG) -> dict:
    config = load_config(config_path)
    results = build(config)
    report = repo_path(config, "classic_report")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(render_report(config, results) + "\n", encoding="utf-8")
    store = repo_path(config, "classic_results")
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(json.dumps(to_jsonable(results), ensure_ascii=False, indent=1), encoding="utf-8")
    figures(config, results)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    config = load_config(args.config)
    results = run(args.config)
    bad = [r for r in crosscheck_rows(results, config["lexical"]) if not r["agree"]]
    print(f"lexicalrichness cross-check: {len(bad)} disagreement(s)")
    for t, r in results.items():
        a = r["views"]["all"]["whole"]
        print(f"{t}: MATTR-500 {a['mattr_500']:.3f}, MTLD {a['mtld']:.1f}, K {a['yule_k']:.1f}, "
              f"R {a['honore_r']:.0f}, words/sentence {r['sentences']['whole']['mean']:.2f}")


if __name__ == "__main__":
    main()
